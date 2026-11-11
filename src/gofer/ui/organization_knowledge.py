"""Organization plans, shared knowledge, typed links and evidence-backed claims.

Plans (mission, projects, milestones, epics) live in the audited configuration, so every
change is a revision. Knowledge entries, links and claims are durable runtime records;
each change appends a hash-linked activity event.

Evidence is checked by Raticode rather than taken from the agent:
- a quote must appear in the cited knowledge entry, and is flagged when the entry changes;
- a file is fingerprinted and flagged when it changes;
- a commit must exist in the ticket's repository;
- a check command is rerun by Raticode only when it is one of the project's configured
  checks, or when the user runs it explicitly.
"""

from __future__ import annotations

import copy
import hashlib
import html
import logging
import re
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from gofer.ui import organization_repository as repository
from gofer.ui.organization_store import (
    Epic,
    Milestone,
    OrganizationConflict,
    now,
    ticket_prefix,
)
from gofer.utils.process import build_subprocess_env, git_push_blocked_environment

KINDS = ("decision", "note", "report", "spec", "finding")
EVIDENCE_KINDS = ("check", "quote", "commit", "file")
MAX_CLAIMS_PER_RUN = 5
MAX_CLAIMS_PER_TASK = 40
MAX_CLAIM_CHARS = 120
MAX_ENTRY_BODY = 100_000
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_CHECK_OUTPUT = 6000
CHECK_TIMEOUT_SECONDS = 900
SOURCE_SUFFIXES = {".md", ".markdown", ".txt", ".html", ".htm"}

PLAN_ACTIONS = {"plan_save", "plan_delete"}
KNOWLEDGE_READ_ACTIONS = {"knowledge_search", "knowledge_read", "ticket_changes", "repository"}
KNOWLEDGE_WRITE_ACTIONS = {
    "knowledge_save",
    "knowledge_import",
    "knowledge_refresh",
    "knowledge_link",
    "claim_add",
    "check_run",
}
ACTIONS = PLAN_ACTIONS | KNOWLEDGE_READ_ACTIONS | KNOWLEDGE_WRITE_ACTIONS
# Only the user or Rem plan, import from the Second Brain or run a check by hand.
OWNER_ACTIONS = PLAN_ACTIONS | {"knowledge_import", "knowledge_refresh", "check_run"}

log = logging.getLogger(__name__)

# One check at a time, so reruns can't swamp the machine while employees work.
_CHECK_SLOT = threading.Semaphore(1)


def short_id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:10]}"


def slugify(text: str, fallback: str = "item") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48].strip("-")
    return slug or fallback


def normalize(text: str) -> str:
    """Compare quotes without caring about whitespace, case or typographic quotes."""
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().casefold()


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def ticket_key(config: dict[str, Any], task: dict[str, Any]) -> str:
    return f"{ticket_prefix(config)}-{task.get('number') or '?'}"


def find_task(org: dict[str, Any], reference: Any) -> Any:
    """Find a task by ID or by its ticket key, such as RAT-12."""
    tasks = org["runtime"]["tasks"]
    if isinstance(reference, str):
        for task in tasks:
            if task["id"] == reference:
                return task
        match = re.fullmatch(r"([A-Z][A-Z0-9]*)-(\d+)", reference.strip().upper())
        if match and match.group(1) == ticket_prefix(org["config"]):
            for task in tasks:
                if task.get("number") == int(match.group(2)):
                    return task
    raise ValueError("Ticket not found")


# --------------------------------------------------------------------------- plans


def _plan_config(org: dict[str, Any], params: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Apply one plan change to a copy of the configuration and describe it."""
    config = copy.deepcopy(org["config"])
    kind, item = params.get("kind"), params.get("item")
    if not isinstance(item, dict):
        raise ValueError("Supply the item to save")
    if kind == "operations":
        allowed = {"operatingHours", "resumeAfterRestart", "workSchedule"}
        if not item or set(item) - allowed:
            raise ValueError("Operations use operatingHours, workSchedule and resumeAfterRestart")
        if "workSchedule" in item:
            from gofer.ui.organization_schedule import checked

            # Checked alone first, so a refusal reads as a sentence, not a field path.
            item = {**item, "workSchedule": checked(item["workSchedule"])}
        config.update({key: item[key] for key in allowed & set(item)})
        changed = [
            label
            for key, label in (
                ("operatingHours", "working hours"),
                ("workSchedule", "the work schedule"),
            )
            if key in item
        ] or ["what happens after a restart"]
        return config, "Changed " + " and ".join(changed)
    if kind == "repository":
        enabled = item.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("Say whether the shared repository is on")
        config["sharedRepository"] = enabled
        return config, (
            "Turned on the shared organization repository"
            if enabled
            else "Turned off the shared organization repository"
        )
    if kind == "mission":
        mission = item.get("mission")
        if not isinstance(mission, str):
            raise ValueError("Mission must be text")
        config["mission"] = mission.strip()
        return config, "Updated the mission"
    if kind == "project":
        projects = config["projects"]
        pid = item.get("id")
        existing = next((p for p in projects if p["id"] == pid), None) if pid else None
        allowed = {
            "name",
            "description",
            "owner",
            "color",
            "workspacePath",
            "checks",
            "autoRunChecks",
        }
        if set(item) - allowed - {"id"}:
            raise ValueError(
                "Projects use name, description, owner, color, workspacePath, checks "
                "and autoRunChecks"
            )
        if existing is None:
            name = str(item.get("name") or "").strip()
            if not name:
                raise ValueError("A project needs a name")
            taken = {p["id"] for p in projects}
            pid = pid or slugify(name, "project")
            base, n = pid, 2
            while pid in taken:
                pid, n = f"{base}-{n}", n + 1
            existing = {"id": pid}
            projects.append(existing)
        for key in allowed & set(item):
            if item[key] is None or item[key] == "" or (key == "autoRunChecks" and not item[key]):
                existing.pop(key, None)
            else:
                existing[key] = item[key]
        if not str(existing.get("name") or "").strip():
            raise ValueError("A project needs a name")
        return config, f"Saved project {existing['name']}"
    if kind in {"milestone", "epic"}:
        model = Milestone if kind == "milestone" else Epic
        collection = config["milestones" if kind == "milestone" else "epics"]
        data = {**item, "id": item.get("id") or short_id("m" if kind == "milestone" else "e")}
        index = next((i for i, x in enumerate(collection) if x["id"] == data["id"]), None)
        if index is not None:
            data = {**collection[index], **item}
        record = model.model_validate(data).model_dump()
        if index is None:
            collection.append(record)
        else:
            collection[index] = record
        return config, f"Saved {kind} {record['title']}"
    raise ValueError("kind must be mission, operations, repository, project, milestone or epic")


def _plan_delete(org: dict[str, Any], params: dict[str, Any]) -> tuple[dict[str, Any], str]:
    config = copy.deepcopy(org["config"])
    kind, iid = params.get("kind"), params.get("id")
    tasks = [t for t in org["runtime"]["tasks"] if t["status"] != "cancelled"]
    if kind == "milestone":
        milestone = next((m for m in config["milestones"] if m["id"] == iid), None)
        if milestone is None:
            raise ValueError("Milestone not found")
        if any(e.get("milestoneId") == iid for e in config["epics"]):
            raise ValueError("Move or delete this milestone's epics first")
        if any(m.get("parentId") == iid for m in config["milestones"]):
            raise ValueError("Project milestones still serve this milestone")
        config["milestones"] = [m for m in config["milestones"] if m["id"] != iid]
        return config, f"Deleted milestone {milestone['title']}"
    if kind == "epic":
        epic = next((e for e in config["epics"] if e["id"] == iid), None)
        if epic is None:
            raise ValueError("Epic not found")
        if count := sum(t.get("epicId") == iid for t in tasks):
            raise ValueError(f"Move this epic's {count} tickets to another epic first")
        config["epics"] = [e for e in config["epics"] if e["id"] != iid]
        return config, f"Deleted epic {epic['title']}"
    if kind == "project":
        project = next((p for p in config["projects"] if p["id"] == iid), None)
        if project is None:
            raise ValueError("Project not found")
        if any(m.get("projectId") == iid for m in config["milestones"]) or any(
            e.get("projectId") == iid for e in config["epics"]
        ):
            raise ValueError("Delete this project's milestones and epics first")
        if any(t.get("project") == iid for t in tasks):
            raise ValueError("Move this project's tickets first")
        config["projects"] = [p for p in config["projects"] if p["id"] != iid]
        for employee in config["employees"]:
            if employee.get("ticketAccess"):
                employee["ticketAccess"].pop(iid, None)
        return config, f"Deleted project {project.get('name') or iid}"
    raise ValueError("kind must be project, milestone or epic")


def plan(
    manager: Any, project: str, org: dict[str, Any], action: str, params: dict[str, Any], actor: str
) -> Any:
    from gofer.ui.organizations import escalation

    config, summary = (_plan_config if action == "plan_save" else _plan_delete)(org, params)
    expected = params.get("expectedRevision")
    if type(expected) is not int:
        raise ValueError("expectedRevision is required")
    reason = str(params.get("reason") or summary)[:4000]
    if actor != "user" and (widening := escalation(org["config"], config)):
        # Same consent path as a configuration change that widens what employees may do.
        manager.store.configure(
            project, org["id"], config, expected, actor, "Consent check", dry_run=True
        )
        return manager._request_consent(
            org,
            actor,
            "configure",
            {"config": config, "expectedRevision": expected, "reason": reason},
            widening,
        )
    with manager._lock:
        result = manager.store.configure(project, org["id"], config, expected, actor, reason)
    manager.stop_revoked(org["id"], result)
    manager.ensure_repository(result)
    return manager.public(result)


# ----------------------------------------------------------------------- knowledge


def entry_text(entry: dict[str, Any]) -> str:
    return "\n".join([entry.get("title", ""), entry.get("summary", ""), entry.get("body", "")])


def _validate_entry(org: dict[str, Any], entry: dict[str, Any]) -> None:
    if entry["kind"] not in KINDS:
        raise ValueError("Knowledge kind must be decision, note, report, spec or finding")
    if not 1 <= len(entry["title"].strip()) <= 200:
        raise ValueError("A knowledge entry needs a title under 200 characters")
    if len(entry["summary"]) > 400:
        raise ValueError("Keep the summary under 400 characters")
    if len(entry["body"]) > MAX_ENTRY_BODY:
        raise ValueError("Knowledge entries are limited to 100000 characters")
    if len(entry["topic"]) > 60:
        raise ValueError("Keep the topic under 60 characters")
    if entry.get("projectId") and entry["projectId"] not in {
        p["id"] for p in org["config"]["projects"]
    }:
        raise ValueError("Unknown project")
    known = org["runtime"].get("knowledge", {})
    links = entry.get("links", [])
    if not isinstance(links, list) or len(links) > 50 or any(k not in known for k in links):
        raise ValueError("Links must name up to 50 existing knowledge entries")


def _summary_of(body: str) -> str:
    """The first paragraph that isn't a heading or front matter."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", body) if b.strip()]
    for block in blocks:
        if not block.startswith(("#", "---")):
            return re.sub(r"\s+", " ", block)[:300]
    return re.sub(r"^#+\s*", "", blocks[0])[:300] if blocks else ""


def _source_text(path: Path) -> tuple[str, str, str]:
    """Read a Second Brain file as (title, text, sha256)."""
    raw = path.read_bytes()
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("Second Brain notes larger than 2 MiB can't be added")
    content = raw.decode("utf-8", errors="replace")
    sha = hashlib.sha256(raw).hexdigest()
    title = path.stem.replace("-", " ").replace("_", " ").strip().capitalize()
    if path.suffix.lower() in {".html", ".htm"}:
        found = re.search(r"<title[^>]*>(.*?)</title>", content, re.S | re.I) or re.search(
            r"<h1[^>]*>(.*?)</h1>", content, re.S | re.I
        )
        if found:
            title = html.unescape(re.sub(r"<[^>]+>", "", found.group(1))).strip() or title
        content = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", content, flags=re.S | re.I)
        content = re.sub(r"<(br|/p|/h[1-6]|/li|/div|/tr)[^>]*>", "\n\n", content, flags=re.I)
        content = html.unescape(re.sub(r"<[^>]+>", " ", content))
        content = re.sub(r"[ \t]+", " ", content)
        content = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", content).strip()
    else:
        content = re.sub(r"\A---\n.*?\n---\n", "", content, flags=re.S)
        heading = re.search(r"^#\s+(.+)$", content, re.M)
        if heading:
            title = heading.group(1).strip()
    return title[:200], content[:MAX_ENTRY_BODY], sha


def _brain_root(org: dict[str, Any]) -> Path:
    brain = org["config"].get("remSecondBrain") or {}
    if brain.get("enabled") is not True or not brain.get("root"):
        raise ValueError("Turn on Second Brain for this organization first")
    root = Path(str(brain["root"])).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("The organization's Second Brain folder is missing")
    return root


def _mirror(org: dict[str, Any], entry: dict[str, Any]) -> dict[str, str] | None:
    """Write the entry into the organization's Second Brain folder as Markdown."""
    if not org["config"].get("knowledgeToSecondBrain") or entry.get("source"):
        return None
    try:
        root = _brain_root(org)
    except ValueError:
        return None
    folder = root / "Organizations" / slugify(org["config"]["slug"], "organization")
    path = folder / f"{slugify(entry['title'], 'note')}-{entry['id'][-6:]}.md"
    previous = (entry.get("mirror") or {}).get("path")
    lines = [
        "---",
        f"title: {entry['title']}",
        f"kind: {entry['kind']}",
        f"organization: {org['config']['name']}",
        f"raticodeKnowledgeId: {entry['id']}",
        f"version: {entry['version']}",
        "---",
        "",
        f"# {entry['title']}",
        "",
        entry["summary"],
        "",
        entry["body"],
        "",
    ]
    content = "\n".join(lines)
    folder.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".md.tmp")
    temporary.write_text(content)
    temporary.replace(path)
    if previous and previous != str(path):
        Path(previous).unlink(missing_ok=True)
    return {"path": str(path), "sha256": digest(content)}


def _save_entry(
    org: dict[str, Any],
    params: dict[str, Any],
    actor: str,
    employee_id: str | None,
    run_id: str | None,
) -> dict[str, Any]:
    item = params.get("entry")
    if not isinstance(item, dict):
        raise ValueError("Supply entry")
    known = org["runtime"].setdefault("knowledge", {})
    current = known.get(item.get("id")) if item.get("id") else None
    if item.get("id") and current is None:
        raise ValueError("Knowledge entry not found")
    if current is not None:
        if params.get("expectedVersion") != current["version"]:
            raise OrganizationConflict("This entry changed. Read it before saving.")
        if employee_id and current["author"] != f"employee:{employee_id}":
            raise ValueError("Employees update only entries they wrote; save a new entry instead")
        if current.get("source") and {"body", "title"} & set(item):
            raise ValueError("Edit Second Brain notes in the Second Brain, then refresh them")
    editable = {"kind", "title", "summary", "body", "topic", "projectId", "links", "archived"}
    if set(item) - editable - {"id"}:
        raise ValueError("Entries use kind, title, summary, body, topic, projectId and links")
    at = now()
    entry = (
        copy.deepcopy(current)
        if current
        else {
            "id": short_id("k"),
            "kind": "note",
            "title": "",
            "summary": "",
            "body": "",
            "topic": "",
            "projectId": None,
            "links": [],
            "author": actor,
            "runId": run_id,
            "taskId": None,
            "createdAt": at,
            "version": 0,
            "history": [],
            "archived": False,
        }
    )
    for key in editable & set(item):
        entry[key] = item[key]
    for key in ("title", "summary", "body", "topic"):
        if not isinstance(entry[key], str):
            raise ValueError(f"{key} must be text")
    entry["title"], entry["topic"] = entry["title"].strip(), entry["topic"].strip()
    if "summary" in item:
        entry["summaryAuto"] = not entry["summary"].strip()
    elif "body" in item and entry.get("summaryAuto"):
        entry["summary"] = ""
    if not entry["summary"].strip():
        # A summary taken from the body follows the body until someone writes one.
        entry["summary"], entry["summaryAuto"] = _summary_of(entry["body"]), True
    entry["archived"] = entry.get("archived") is True
    entry["links"] = list(dict.fromkeys(x for x in entry["links"] if x != entry["id"]))
    if run_id and current is None:
        entry["taskId"] = next(
            (t["id"] for t in org["runtime"]["tasks"] if t["claim"] == run_id), None
        )
    _validate_entry(org, entry)
    sha = digest(entry_text(entry))
    if current is not None and sha == current.get("sha256") and entry == current:
        return entry
    entry.update(version=entry["version"] + 1, sha256=sha, updatedAt=at, updatedBy=actor)
    entry["history"] = [
        *entry["history"],
        {"version": entry["version"], "sha256": sha, "at": at, "actor": actor},
    ][-50:]
    known[entry["id"]] = entry
    return entry


def _import_entry(org: dict[str, Any], params: dict[str, Any], actor: str) -> Any:
    root = _brain_root(org)
    value = params.get("path")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Choose a Second Brain note")
    path = Path(value).expanduser()
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Choose a note inside the organization's Second Brain folder")
    if path.suffix.lower() not in SOURCE_SUFFIXES:
        raise ValueError("Add Markdown, text or HTML notes")
    title, text, sha = _source_text(path)
    known = org["runtime"].setdefault("knowledge", {})
    existing = next(
        (e for e in known.values() if (e.get("source") or {}).get("path") == str(path)), None
    )
    at = now()
    if existing is None:
        existing = {
            "id": short_id("k"),
            "kind": params.get("kind") if params.get("kind") in KINDS else "note",
            "title": title,
            "summary": _summary_of(text),
            "body": text,
            "topic": str(params.get("topic") or "")[:60],
            "projectId": params.get("projectId") or None,
            "links": [],
            "author": actor,
            "runId": None,
            "taskId": None,
            "createdAt": at,
            "version": 0,
            "history": [],
            "archived": False,
        }
    elif existing["source"].get("sha256") == sha:
        return existing
    existing.update(
        title=title,
        body=text,
        summary=existing.get("summary") or _summary_of(text),
        source={"type": "secondBrain", "path": str(path), "sha256": sha, "readAt": at},
    )
    existing.pop("sourceMissing", None)
    _validate_entry(org, existing)
    existing.update(
        version=existing["version"] + 1,
        sha256=digest(entry_text(existing)),
        updatedAt=at,
        updatedBy=actor,
        changedAt=at if existing["version"] else None,
    )
    existing["history"] = [
        *existing["history"],
        {"version": existing["version"], "sha256": existing["sha256"], "at": at, "actor": actor},
    ][-50:]
    known[existing["id"]] = existing
    return existing


def _refresh(org: dict[str, Any], actor: str) -> dict[str, Any]:
    changed, missing = [], []
    for entry in list(org["runtime"].get("knowledge", {}).values()):
        source = entry.get("source")
        if not source:
            continue
        path = Path(source["path"])
        if not path.is_file():
            if not entry.get("sourceMissing"):
                entry["sourceMissing"] = True
                missing.append(entry["id"])
            continue
        before = entry["version"]
        _import_entry(org, {"path": str(path)}, actor)
        if entry["version"] != before:
            changed.append(entry["id"])
    return {"changed": changed, "missing": missing}


def search(org: dict[str, Any], params: dict[str, Any]) -> list[dict[str, Any]]:
    words = [w for w in normalize(str(params.get("query") or "")).split(" ") if w]
    limit = params.get("limit", 20)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    results = []
    for entry in org["runtime"].get("knowledge", {}).values():
        if entry.get("archived"):
            continue
        if params.get("projectId") and entry.get("projectId") not in {params["projectId"], None}:
            continue
        if params.get("kind") and entry["kind"] != params["kind"]:
            continue
        title, text = normalize(entry["title"]), normalize(entry_text(entry))
        score = sum(3 if w in title else 1 if w in text else 0 for w in words)
        if words and not score:
            continue
        results.append((score, entry["updatedAt"], entry))
    results.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [
        {k: e.get(k) for k in ("id", "kind", "title", "summary", "topic", "projectId", "version")}
        for _, _, e in results[:limit]
    ]


def _record_read(org: dict[str, Any], entry: dict[str, Any], run_id: str) -> dict[str, Any] | None:
    """Remember that a run read an entry: an observed link from its ticket."""
    task = next((t for t in org["runtime"]["tasks"] if t["claim"] == run_id), None)
    if task is None:
        return None
    links = org["runtime"].setdefault("links", {})
    for link in links.values():
        if link["kind"] == "read" and link["runId"] == run_id and link["to"] == f"kb:{entry['id']}":
            return None
    link = {
        "id": short_id("l"),
        "kind": "read",
        "origin": "observed",
        "from": f"ticket:{task['id']}",
        "to": f"kb:{entry['id']}",
        "runId": run_id,
        "version": entry["version"],
        "actor": "system",
        "at": now(),
    }
    links[link["id"]] = link
    return link


def _add_link(
    org: dict[str, Any],
    params: dict[str, Any],
    actor: str,
    employee_id: str | None,
    run_id: str | None,
) -> Any:
    links = org["runtime"].setdefault("links", {})
    if params.get("remove"):
        link = links.get(params.get("linkId"))
        if link is None or link["kind"] != "asserted":
            raise ValueError("Choose an agent link to remove")
        if employee_id and link["actor"] != actor:
            raise ValueError("Employees remove only their own links")
        link["removed"] = {"actor": actor, "at": now()}
        return link
    known = org["runtime"].get("knowledge", {})
    source, target = known.get(params.get("from")), known.get(params.get("to"))
    if source is None or target is None or source is target:
        raise ValueError("Link two different knowledge entries")
    quote = params.get("quote")
    if not isinstance(quote, str) or not 8 <= len(quote.strip()) <= 1000:
        raise ValueError("Support the link with a quote of 8 to 1000 characters from either entry")
    holder = next(
        (e for e in (source, target) if normalize(quote) in normalize(entry_text(e))), None
    )
    if holder is None:
        raise ValueError("That quote isn't in either entry. Quote the text exactly.")
    for link in links.values():
        if (
            link["kind"] == "asserted"
            and not link.get("removed")
            and {link["from"], link["to"]} == {f"kb:{source['id']}", f"kb:{target['id']}"}
        ):
            return link
    link = {
        "id": short_id("l"),
        "kind": "asserted",
        "origin": "agent" if employee_id else "user" if actor == "user" else "rem",
        "from": f"kb:{source['id']}",
        "to": f"kb:{target['id']}",
        "quote": quote.strip(),
        "quotedFrom": holder["id"],
        "version": holder["version"],
        "runId": run_id,
        "actor": actor,
        "at": now(),
    }
    links[link["id"]] = link
    return link


# -------------------------------------------------------------------------- claims


def _task_root(org: dict[str, Any], task: dict[str, Any]) -> str:
    from gofer.ui.organization_operations import workspace

    worktree = task.get("worktree") or {}
    if worktree.get("root") and Path(worktree["root"]).is_dir():
        return str(worktree["root"])
    return workspace(org, task)


def _git(root: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", root, *args],
        env=build_subprocess_env(),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def _commit_files(root: str, sha: str) -> list[list[Any]]:
    shown = _git(root, "show", "--numstat", "--format=", sha)
    files = []
    for line in shown.stdout.splitlines()[:200]:
        parts = line.split("\t")
        if len(parts) == 3:
            added, deleted = (int(x) if x.isdigit() else 0 for x in parts[:2])
            files.append([parts[2], added, deleted])
    return files


def project_checks(org: dict[str, Any], task: dict[str, Any]) -> list[dict[str, str]]:
    project: dict[str, Any] = next(
        (p for p in org["config"]["projects"] if p["id"] == task.get("project")), {}
    )
    return list(project.get("checks") or [])


def _prepare_evidence(
    org: dict[str, Any], task: dict[str, Any], evidence: Any, employee_id: str | None
) -> dict[str, Any]:
    from gofer.ui.organization_operations import file_evidence

    if evidence is None:
        return {"kind": None, "state": "none"}
    if not isinstance(evidence, dict) or evidence.get("kind") not in EVIDENCE_KINDS:
        raise ValueError("Evidence kind must be check, quote, commit or file")
    kind = evidence["kind"]
    if kind == "quote":
        entry = org["runtime"].get("knowledge", {}).get(evidence.get("entryId"))
        quote = evidence.get("quote")
        if entry is None:
            raise ValueError("Quote evidence must name a knowledge entry")
        if not isinstance(quote, str) or not 8 <= len(quote.strip()) <= 1000:
            raise ValueError("Quote 8 to 1000 characters from the entry")
        if normalize(quote) not in normalize(entry_text(entry)):
            raise ValueError("That quote isn't in the entry. Quote the text exactly.")
        return {
            "kind": kind,
            "entryId": entry["id"],
            "quote": quote.strip(),
            "version": entry["version"],
            "state": "ok",
        }
    if kind == "file":
        found = file_evidence(org, str(evidence.get("path", "")), employee_id)
        return {"kind": kind, "path": found["path"], "sha256": found["sha256"], "state": "ok"}
    root = _task_root(org, task)
    if kind == "commit":
        sha = evidence.get("sha")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{7,40}", sha):
            raise ValueError("Commit evidence needs a commit hash")
        resolved = _git(root, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
        if resolved.returncode:
            return {"kind": kind, "sha": sha, "state": "bad", "note": "Commit not found"}
        full = resolved.stdout.strip()
        on_branch = _git(root, "merge-base", "--is-ancestor", full, "HEAD").returncode == 0
        return {
            "kind": kind,
            "sha": full,
            "files": _commit_files(root, full),
            "onBranch": on_branch,
            "state": "ok" if on_branch else "warn",
            "note": "" if on_branch else "Not on the ticket's branch",
        }
    command = evidence.get("command")
    if not isinstance(command, str) or not 1 <= len(command.strip()) <= 1000:
        raise ValueError("Check evidence needs the command that was run")
    command = command.strip()
    configured = next(
        (
            c
            for c in project_checks(org, task)
            if command in {c["command"].strip(), c["name"].strip()}
        ),
        None,
    )
    reported = evidence.get("reported")
    return {
        "kind": kind,
        "command": configured["command"].strip() if configured else command,
        "checkName": configured["name"] if configured else None,
        "reported": str(reported)[:500] if reported is not None else None,
        # Raticode reruns project checks itself; anything else waits for the user.
        "state": "queued" if configured else "unverified",
    }


def _add_claim(
    org: dict[str, Any],
    params: dict[str, Any],
    actor: str,
    employee_id: str | None,
    run_id: str | None,
) -> dict[str, Any]:
    task = find_task(org, params.get("taskId"))
    if employee_id and task["claim"] != run_id:
        raise ValueError("Post claims about the ticket you are working on")
    text = params.get("text")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_CLAIM_CHARS:
        raise ValueError(f"A claim is one sentence of at most {MAX_CLAIM_CHARS} characters")
    claims = org["runtime"].setdefault("claims", {})
    current = [c for c in claims.values() if c["taskId"] == task["id"]]
    if run_id and sum(c.get("runId") == run_id for c in current) >= MAX_CLAIMS_PER_RUN:
        raise ValueError(f"A run can post at most {MAX_CLAIMS_PER_RUN} claims")
    if len(current) >= MAX_CLAIMS_PER_TASK:
        raise ValueError("This ticket has too many claims; review it first")
    claim = {
        "id": short_id("c"),
        "taskId": task["id"],
        "runId": run_id,
        "text": text.strip(),
        "evidence": _prepare_evidence(org, task, params.get("evidence"), employee_id),
        "actor": actor,
        "at": now(),
    }
    claims[claim["id"]] = claim
    return claim


def claim_state(org: dict[str, Any], claim: dict[str, Any]) -> dict[str, Any]:
    """The evidence state now: ok, warn, bad, none, queued, running or unverified."""
    from gofer.ui.organization_operations import file_evidence

    evidence = dict(claim.get("evidence") or {"state": "none"})
    if evidence.get("kind") == "quote":
        entry = org["runtime"].get("knowledge", {}).get(evidence["entryId"])
        if entry is None:
            evidence.update(state="bad", note="The note was removed")
        elif normalize(evidence["quote"]) in normalize(entry_text(entry)):
            evidence["state"] = "ok"
        else:
            evidence.update(
                state="warn",
                note="The note changed after it was quoted",
                now=entry.get("summary") or entry["body"][:400],
            )
    elif evidence.get("kind") == "file":
        try:
            found = file_evidence(org, evidence["path"], None)
            changed = found["sha256"] != evidence["sha256"]
        except (OSError, ValueError):
            changed = True
        if changed:
            evidence.update(state="warn", note="The file changed after it was cited")
    return evidence


def annotate(org: dict[str, Any]) -> None:
    """Add current evidence states to claims and links for display."""
    runtime = org["runtime"]
    for claim in runtime.get("claims", {}).values():
        claim["evidence"] = claim_state(org, claim)
    knowledge = runtime.get("knowledge", {})
    for link in runtime.get("links", {}).values():
        if link["kind"] == "asserted":
            holder = knowledge.get(link.get("quotedFrom"))
            link["state"] = (
                "ok"
                if holder and normalize(link["quote"]) in normalize(entry_text(holder))
                else "warn"
            )
    prefix = ticket_prefix(org["config"])
    for task in runtime["tasks"]:
        task["key"] = f"{prefix}-{task.get('number') or '?'}"


def acceptance_evidence(org: dict[str, Any], task: dict[str, Any], note: str) -> str:
    """Accepted work's evidence: the reviewer's note and each claim with its checked state."""
    labels = {
        "ok": "verified",
        "warn": "source changed",
        "bad": "failed",
        "none": "no evidence",
        "queued": "check queued",
        "running": "checking",
        "unverified": "not rerun",
    }
    lines = [f"Accepted after review: {note.strip()}"]
    for claim in org["runtime"].get("claims", {}).values():
        if claim["taskId"] == task["id"] and not claim.get("supersededAt"):
            state = claim_state(org, claim).get("state", "none")
            lines.append(f"- {claim['text']} ({labels.get(state, state)})")
    return "\n".join(lines)[:100_000]


def supersede_claims(org: dict[str, Any], task_id: str, actor: str) -> None:
    """Sending work back clears its claims for the next run; the records stay."""
    for claim in org["runtime"].get("claims", {}).values():
        if claim["taskId"] == task_id and not claim.get("supersededAt"):
            claim.update(supersededAt=now(), supersededBy=actor)


def _check_root(org: dict[str, Any], task: dict[str, Any]) -> str:
    root = _task_root(org, task)
    if not Path(root).is_dir():
        raise ValueError("The ticket's folder is no longer available")
    return root


def run_check(manager: Any, project: str, oid: str, claim_id: str, actor: str) -> dict[str, Any]:
    """Run a claim's check command in the ticket's worktree and record the result."""
    org = manager.store.get(project, oid)
    claim = org["runtime"].get("claims", {}).get(claim_id)
    if claim is None or (claim.get("evidence") or {}).get("kind") != "check":
        raise ValueError("Choose a claim with a check")
    task = next(t for t in org["runtime"]["tasks"] if t["id"] == claim["taskId"])
    root = _check_root(org, task)
    command = claim["evidence"]["command"]

    def mark(state: dict[str, Any]) -> None:
        def update(current: dict[str, Any]) -> dict[str, Any]:
            stored = current["runtime"].get("claims", {}).get(claim_id)
            if stored is None:
                return {}
            stored["evidence"] = {**stored["evidence"], **state}
            return {
                "claimId": claim_id,
                "taskId": claim["taskId"],
                "state": state["state"],
                **({"exitCode": state["exitCode"]} if "exitCode" in state else {}),
            }

        manager.store.mutate(
            project,
            oid,
            actor,
            "check_finished" if "exitCode" in state else "check_started",
            update,
        )

    with _CHECK_SLOT:
        mark({"state": "running", "startedAt": now()})
        started = datetime.now(UTC)
        env = build_subprocess_env()
        env.update(git_push_blocked_environment(env))
        try:
            completed = subprocess.run(
                ["bash", "-c", command],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=min(CHECK_TIMEOUT_SECONDS, org["config"].get("turnTimeoutSeconds", 1800)),
                check=False,
            )
            code, output = completed.returncode, completed.stdout + completed.stderr
        except subprocess.TimeoutExpired as exc:
            code = 124
            output = str(exc.stdout or "") + "\nThe check timed out."
        except OSError as exc:
            code, output = 127, str(exc)
        head = _git(root, "rev-parse", "HEAD").stdout.strip()
        result = {
            "state": "ok" if code == 0 else "bad",
            "exitCode": code,
            "output": manager._redact(output[-MAX_CHECK_OUTPUT:]),
            "ranAt": now(),
            "ranBy": "you" if actor == "user" else "Raticode",
            "seconds": round((datetime.now(UTC) - started).total_seconds(), 1),
            "headCommit": head,
        }
        mark(result)
    return result


def queued_checks(org: dict[str, Any], task_id: str) -> list[str]:
    return [
        c["id"]
        for c in org["runtime"].get("claims", {}).values()
        if c["taskId"] == task_id
        and not c.get("supersededAt")
        and (c.get("evidence") or {}).get("state") == "queued"
    ]


def auto_checks(org: dict[str, Any], task: dict[str, Any]) -> bool:
    project: dict[str, Any] = next(
        (p for p in org["config"]["projects"] if p["id"] == task.get("project")), {}
    )
    return project.get("autoRunChecks") is True


def start_checks(
    manager: Any,
    project: str,
    oid: str,
    task_id: str,
    actor: str = "system",
    claim_ids: list[str] | None = None,
) -> threading.Thread | None:
    """Rerun a ticket's queued project checks in the background.

    Checks run the ticket branch's code as the user, outside the worker's sandbox, so
    Raticode starts them on its own only for projects that opted in; otherwise the user
    starts them from the ticket.
    """
    org = manager.store.get(project, oid)
    task = next((t for t in org["runtime"]["tasks"] if t["id"] == task_id), None)
    if task is None or (actor == "system" and not auto_checks(org, task)):
        return None
    pending = claim_ids if claim_ids is not None else queued_checks(org, task_id)
    if not pending:
        return None

    def work() -> None:
        for claim_id in pending:
            try:
                run_check(manager, project, oid, claim_id, actor)
            except Exception:  # noqa: BLE001 - one failed check must not stop the others
                continue

    thread = threading.Thread(target=work, name=f"org-checks-{task_id[:8]}", daemon=True)
    thread.start()
    return thread


def ticket_changes(org: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    """The branch, commits and changed files of a ticket's worktree."""
    worktree = task.get("worktree") or task.get("releasedWorktree") or {}
    branch, base = worktree.get("branch"), worktree.get("base")
    if not branch:
        return {"branch": None, "files": [], "commits": []}
    repository = worktree.get("repository") or _task_root(org, task)
    if not Path(repository).is_dir():
        return {"branch": branch, "files": [], "commits": [], "missing": True}
    files = []
    numstat = _git(repository, "diff", "--numstat", f"{base}...{branch}")
    for line in numstat.stdout.splitlines()[:300]:
        parts = line.split("\t")
        if len(parts) == 3:
            files.append([parts[2], *(int(x) if x.isdigit() else 0 for x in parts[:2])])
    log = _git(repository, "log", "--format=%H%x09%s", f"{base}..{branch}", "-n", "30")
    commits = [line.split("\t", 1) for line in log.stdout.splitlines() if "\t" in line]
    return {"branch": branch, "base": base, "files": files, "commits": commits}


# ------------------------------------------------------------------------ dispatch


def knowledge_action(
    manager: Any,
    project: str,
    org: dict[str, Any],
    action: str,
    params: dict[str, Any],
    actor: str,
    employee_id: str | None,
    run_id: str | None,
) -> Any:
    if action in OWNER_ACTIONS and employee_id:
        raise ValueError("Only the user or Rem can do that; ask them in a comment")
    if action in PLAN_ACTIONS:
        return plan(manager, project, org, action, params, actor)
    if action == "check_run":
        # Checks run the branch's code as the user, so only the user starts them by hand.
        if actor != "user":
            raise ValueError("Only you can run checks by hand")
        claims = org["runtime"].get("claims", {})
        if isinstance(params.get("claimId"), str):
            claim = claims.get(params["claimId"])
            if claim is None or (claim.get("evidence") or {}).get("kind") != "check":
                raise ValueError("Choose a claim with a check")
            if claim["evidence"].get("state") == "running":
                raise ValueError("This check is already running")
            task_id, selected = claim["taskId"], [claim["id"]]
        else:
            task_id = find_task(org, params.get("taskId"))["id"]
            selected = queued_checks(org, task_id)
            if not selected:
                raise ValueError("This ticket has no checks waiting to run")
        start_checks(manager, project, org["id"], task_id, actor, selected)
        return {"started": len(selected), "taskId": task_id}
    if action == "knowledge_search":
        return {"items": search(org, params)}
    if action == "ticket_changes":
        task = find_task(org, params.get("taskId"))
        changed = ticket_changes(org, task)
        if task.get("sharedWorktree"):
            changed["shared"] = repository.changes(task["sharedWorktree"])
        return changed
    if action == "repository":
        return repository.state(manager.data_dir, org)
    if action == "knowledge_read" and not run_id:
        entry = org["runtime"].get("knowledge", {}).get(params.get("id"))
        if entry is None:
            raise ValueError("Knowledge entry not found")
        return entry
    result: dict[str, Any] = {}
    queued: list[str] = []

    def mutate(current: dict[str, Any]) -> dict[str, Any]:
        runtime = current["runtime"]
        if run_id is not None and not any(t["claim"] == run_id for t in runtime["tasks"]):
            raise OrganizationConflict("This employee turn has been revoked")
        if action == "knowledge_read":
            entry = runtime.get("knowledge", {}).get(params.get("id"))
            if entry is None:
                raise ValueError("Knowledge entry not found")
            result.update(copy.deepcopy(entry))
            link = _record_read(current, entry, str(run_id))
            return {"entryId": entry["id"], "linkId": link["id"]} if link else {}
        if action == "knowledge_save":
            entry = _save_entry(current, params, actor, employee_id, run_id)
            mirror = _mirror(current, entry)
            if mirror:
                entry["mirror"] = mirror
            result.update(copy.deepcopy(entry))
            return {
                "entryId": entry["id"],
                "title": entry["title"][:200],
                "version": entry["version"],
            }
        if action == "knowledge_import":
            entry = _import_entry(current, params, actor)
            result.update(copy.deepcopy(entry))
            return {
                "entryId": entry["id"],
                "title": entry["title"][:200],
                "version": entry["version"],
                "path": entry["source"]["path"],
            }
        if action == "knowledge_refresh":
            result.update(_refresh(current, actor))
            return dict(result) if result["changed"] or result["missing"] else {}
        if action == "knowledge_link":
            link = _add_link(current, params, actor, employee_id, run_id)
            result.update(copy.deepcopy(link))
            return {
                "linkId": link["id"],
                "from": link["from"],
                "to": link["to"],
                "removed": bool(link.get("removed")),
            }
        if action == "claim_add":
            claim = _add_claim(current, params, actor, employee_id, run_id)
            result.update(copy.deepcopy(claim))
            task = next(t for t in runtime["tasks"] if t["id"] == claim["taskId"])
            if claim["evidence"].get("state") == "queued" and not task["claim"]:
                queued.append(task["id"])
            return {
                "claimId": claim["id"],
                "taskId": claim["taskId"],
                "evidence": claim["evidence"].get("kind"),
                "state": claim["evidence"].get("state"),
            }
        raise ValueError("Unknown knowledge action")

    manager.store.mutate(project, org["id"], actor, action, mutate)
    for task_id in queued:
        start_checks(manager, project, org["id"], task_id)
    if action in {"knowledge_save", "knowledge_import", "knowledge_refresh"}:
        _mirror_to_repository(manager, project, org["id"], action, result)
    return result


def _mirror_to_repository(
    manager: Any, project: str, oid: str, action: str, result: dict[str, Any]
) -> None:
    """Keep the shared repository's knowledge folder in step. Never fails the save."""
    current = manager.store.get(project, oid)
    known = current["runtime"].get("knowledge", {})
    ids = result.get("changed", []) if action == "knowledge_refresh" else [result.get("id")]
    for entry_id in ids:
        if entry_id in known:
            try:
                repository.sync_entry(manager.data_dir, current, known[entry_id])
            except (repository.RepositoryError, OSError):
                log.exception("ORGANIZATION_REPOSITORY_SYNC_FAILED organization=%s", oid)


def run_context(org: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    """What a run should know about where its ticket sits in the plan."""
    config = org["config"]
    epic = next((e for e in config.get("epics", []) if e["id"] == task.get("epicId")), None)
    milestones = {m["id"]: m for m in config.get("milestones", [])}
    milestone = milestones.get(epic["milestoneId"]) if epic and epic.get("milestoneId") else None
    serves = (
        milestones.get(milestone["parentId"]) if milestone and milestone.get("parentId") else None
    )
    project = next((p for p in config["projects"] if p["id"] == task.get("project")), None)
    knowledge = org["runtime"].get("knowledge", {})
    return {
        "ticket": ticket_key(config, task),
        "mission": config.get("mission", ""),
        "project": project and {k: project.get(k) for k in ("id", "name", "description")},
        "milestone": milestone and {k: milestone[k] for k in ("id", "title", "dueAt")},
        "servesOrganizationMilestone": serves and serves["title"],
        "epic": epic and {k: epic[k] for k in ("id", "title", "description")},
        "doneWhen": task.get("doneWhen", ""),
        "linkedKnowledge": [
            {k: knowledge[k_id].get(k) for k in ("id", "kind", "title", "summary")}
            for k_id in task.get("knowledge", [])
            if k_id in knowledge
        ],
        "projectChecks": [c["name"] + ": " + c["command"] for c in project_checks(org, task)],
        "ticketPermissions": ticket_permissions(org, task.get("assignee")),
    }


def ticket_permissions(org: dict[str, Any], employee_id: str | None) -> Any:
    """A worker's ticket permissions by project name, or "unrestricted"."""
    employee = next((e for e in org["config"]["employees"] if e["id"] == employee_id), None)
    access = (employee or {}).get("ticketAccess")
    if access is None:
        return "unrestricted"
    names = {p["id"]: p.get("name") or p["id"] for p in org["config"]["projects"]}
    return {
        (names.get(key, key) if key != "*" else "tickets outside a project"): actions
        for key, actions in access.items()
    }


RUN_GUIDE = (
    "Organization knowledge: use knowledge_search and knowledge_read to consult shared notes "
    "before deciding; Raticode records which notes you read. Save lasting decisions, specs and "
    "findings with knowledge_save (short title, one-sentence summary). Link related notes with "
    "knowledge_link and an exact quote. Before you finish, post at most 5 claims with "
    "claim_add: one sentence each (under 120 characters) with evidence: "
    "{kind: check, command} using one of projectChecks so Raticode reruns it, "
    "{kind: quote, entryId, quote} quoting a note exactly, {kind: commit, sha}, or "
    "{kind: file, path}. Claims without evidence are shown to the user as unverified.\n"
    "Planning: create tickets for follow-up work with task_create, status=backlog for ideas "
    "or status=todo to queue them. Raticode starts To do tickets for their assignee, one at a "
    "time, within working hours. To work on a ticket next, task_update it to "
    "status=in_progress; it goes to the front of its worker's queue and starts when that "
    "worker is free. You can take an unassigned ticket by setting assignee to yourself.\n"
)
