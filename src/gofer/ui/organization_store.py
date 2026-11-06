"""Transactional organization definitions, immutable revisions and durable work."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gofer.core.prompt_envelope import AgentResources
from gofer.core.provider_permissions import provider_permission_args
from gofer.ui.organization_hours import OperatingHours
from gofer.utils.process import build_subprocess_env


def project_identity(value: str) -> tuple[str, str]:
    """Identify all worktrees by their shared Git directory; folders by real path."""
    path = Path(value).expanduser().resolve()
    markers = []
    for directory in (path, *path.parents):
        marker = directory / ".git"
        try:
            stat = marker.stat()
            markers.append((str(marker), stat.st_ino, stat.st_mtime_ns, stat.st_size))
            break
        except OSError:
            continue
    return _project_identity(str(path), tuple(markers))


@lru_cache(maxsize=512)
def _project_identity(value: str, marker: tuple[Any, ...]) -> tuple[str, str]:
    path = Path(value)
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            env=build_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        common = str(Path(result.stdout.strip()).resolve())
        trees = subprocess.run(
            ["git", "-C", str(path), "worktree", "list", "--porcelain", "-z"],
            env=build_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout
        main = trees.split("\0", 1)[0].removeprefix("worktree ")
        return f"git:{common}", str(Path(main).resolve())
    except (OSError, subprocess.SubprocessError):
        return f"folder:{path}", str(path)


DEFAULT_MONTHLY_TURN_LIMIT = 200
DEFAULT_CHILD_TASKS_PER_TURN = 5
DEFAULT_DELEGATION_DEPTH = 3
# Modes that bypass the provider sandbox or its approvals. Rem needs the user's consent.
DANGEROUS_PERMISSION_MODES = frozenset({"danger-full-access", "bypassPermissions"})


# New or changed heartbeats can't wake an employee more often than this. Saved shorter
# values keep working until they are edited, so existing configurations still load.
MIN_HEARTBEAT_SECONDS = 900


def check_heartbeats(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> None:
    previous = {e.get("id"): e.get("heartbeatSeconds") or 0 for e in before}
    for employee in after:
        beat = employee.get("heartbeatSeconds") or 0
        if 0 < beat < MIN_HEARTBEAT_SECONDS and previous.get(employee.get("id")) != beat:
            raise ValueError(
                f"{employee.get('name') or employee.get('id')}'s heartbeat must be at least "
                "15 minutes (900 seconds) apart"
            )


def event_digest(previous: str, oid: str, at: str, actor: str, kind: str, body: str) -> str:
    return hashlib.sha256(json.dumps([previous, oid, at, actor, kind, body]).encode()).hexdigest()


def now() -> str:
    return datetime.now(UTC).isoformat()


class OrganizationConflict(ValueError):
    """A concurrent editor or worker already changed this record."""


class ExecutionTarget(BaseModel):
    """Owner-granted destination. Employees refer to its ID, never arbitrary endpoints."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$")
    kind: Literal["swarm", "fleet", "remote"]
    employees: list[str] = Field(min_length=1)
    swarmId: str | None = None
    deviceId: str | None = None
    threadId: str | None = None
    projectId: str | None = None
    url: str | None = None
    secretRef: str | None = None
    turnLimit: int = Field(default=1, ge=1, le=1000)

    @model_validator(mode="after")
    def destination(self) -> ExecutionTarget:
        from urllib.parse import urlsplit
        from uuid import UUID

        if self.kind == "swarm" and not self.swarmId:
            raise ValueError("A swarm target needs swarmId")
        if self.kind == "fleet":
            for value in (self.deviceId, self.threadId, self.projectId):
                if not value or str(UUID(value)) != value:
                    raise ValueError("Fleet targets need canonical device/thread/project UUIDs")
            if self.turnLimit != 1:
                raise ValueError("A fleet job reserves one provider turn")
        if self.kind == "remote":
            url = urlsplit(self.url or "")
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or not self.secretRef
            ):
                raise ValueError("Remote targets need an HTTPS base URL and secretRef")
        return self


TicketAction = Literal["create", "edit", "move", "cancel"]
TICKET_ACTIONS: list[str] = ["create", "edit", "move", "cancel"]


def ticket_allowed(employee: dict[str, Any], project: str | None, action: str) -> bool:
    """Whether a worker's ticket permissions allow an action in a project."""
    access = employee.get("ticketAccess")
    if access is None:
        return True
    return action in access.get(project or "*", [])


class Employee(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$")
    name: str = Field(min_length=1, max_length=150)
    role: str = ""
    title: str = ""
    reportsTo: str | None = None
    instructions: str = Field(default="", max_length=100_000)
    provider: str = "codex"
    model: str = "cli-default"
    effort: str | None = None
    permissionMode: str = "workspace-write"
    resources: AgentResources = Field(default_factory=AgentResources)
    skills: list[str] = Field(default_factory=list)
    paused: bool = False
    heartbeatSeconds: int = Field(default=0, ge=0, le=86400)
    memory: str = Field(default="", max_length=100_000)
    monthlyBudgetUsd: float = Field(default=0, ge=0, allow_inf_nan=False)
    monthlyTurnLimit: int = Field(default=0, ge=0, le=1000000)
    monthlyTokenLimit: int = Field(default=0, ge=0, le=100_000_000_000)
    workspacePaths: list[str] | None = None
    secretRefs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # How this worker thinks and works, in a sentence or two, and what they own.
    persona: str = Field(default="", max_length=500)
    responsibilities: list[str] = Field(default_factory=list, max_length=20)
    # Per-project ticket permissions. None keeps the organization-wide rules. Otherwise each
    # project ID, or "*" for tickets outside any project, lists what this worker may do.
    ticketAccess: dict[str, list[TicketAction]] | None = None

    @model_validator(mode="before")
    @classmethod
    def default_permission(cls, value: Any) -> Any:
        if isinstance(value, dict) and "permissionMode" not in value:
            value = {
                **value,
                "permissionMode": (
                    "workspace-write" if value.get("provider", "codex") == "codex" else "dontAsk"
                ),
            }
        return value

    @model_validator(mode="after")
    def valid_provider(self) -> Employee:
        provider_permission_args(self.provider, self.permissionMode)
        if self.heartbeatSeconds and self.heartbeatSeconds < 60:
            raise ValueError("Heartbeats must be at least 60 seconds apart")
        if not self.name.strip():
            raise ValueError("Employee name is required")
        if any(s.name in {"organizations", "rem_threads"} for s in self.resources.mcpServers):
            raise ValueError("Organization and thread tools are supplied by the runtime")
        self.responsibilities = [r.strip() for r in self.responsibilities if r.strip()]
        if self.ticketAccess is not None:
            self.ticketAccess = {
                key: sorted(set(actions), key=TICKET_ACTIONS.index)
                for key, actions in self.ticketAccess.items()
            }
        if any(len(r) > 200 for r in self.responsibilities):
            raise ValueError("Keep each responsibility under 200 characters")
        return self


class Goal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=500)
    parentId: str | None = None
    owner: str | None = None
    level: Literal["company", "team", "individual"] = "company"
    status: Literal["active", "achieved", "cancelled"] = "active"


PORTABLE_ID = r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$"
DATE = r"^\d{4}-\d{2}-\d{2}$"


class Milestone(BaseModel):
    """A dated outcome. Without projectId it belongs to the whole organization; project
    milestones may serve one organization milestone through parentId."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=PORTABLE_ID)
    title: str = Field(min_length=1, max_length=200)
    short: str = Field(default="", max_length=40)
    dueAt: str | None = Field(default=None, pattern=DATE)
    projectId: str | None = None
    parentId: str | None = None
    status: Literal["active", "done", "cancelled"] = "active"


class Epic(BaseModel):
    """A body of work inside a project milestone. Tickets point at it through epicId."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=PORTABLE_ID)
    title: str = Field(min_length=1, max_length=200)
    milestoneId: str | None = None
    projectId: str | None = None
    owner: str | None = None
    description: str = Field(default="", max_length=4000)
    status: Literal["active", "done", "cancelled"] = "active"


class OrganizationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    # One line the whole organization works toward. Shown first and given to every run.
    mission: str = Field(default="", max_length=500)
    instructions: str = ""
    slug: str = Field(default="organization", pattern=r"^[a-z0-9][a-z0-9_-]{0,99}$")
    employees: list[Employee] = Field(default_factory=list, max_length=100)
    goals: list[str] = Field(default_factory=list)
    goalRecords: list[Goal] = Field(default_factory=list)
    budgetWarningPercent: int = Field(default=80, ge=1, le=100)
    # Agent Companies calls initiatives "projects" in its portable format.
    projects: list[dict[str, Any]] = Field(
        default_factory=list, description="Initiatives, using the Agent Companies projects field"
    )
    projectRoots: list[str] = Field(
        default_factory=list,
        max_length=100,
        description="Owned Raticode project folders and all their worktrees. First is default.",
    )
    teams: list[dict[str, Any]] = Field(default_factory=list)
    monthlyBudgetUsd: float = Field(default=0, ge=0, allow_inf_nan=False)
    # New organizations get a bounded month; 0 remains an explicit "unlimited".
    monthlyTurnLimit: int = Field(default=DEFAULT_MONTHLY_TURN_LIMIT, ge=0, le=1000000)
    monthlyTokenLimit: int = Field(default=0, ge=0, le=100_000_000_000)
    maxConcurrency: int = Field(default=1, ge=1, le=16)
    maxTaskTurns: int = Field(default=20, ge=1, le=1000)
    turnTimeoutSeconds: int = Field(default=1800, ge=30, le=86400)
    # Limits on work employees create for each other during one run.
    maxChildTasksPerTurn: int = Field(default=DEFAULT_CHILD_TASKS_PER_TURN, ge=0, le=100)
    maxDelegationDepth: int = Field(default=DEFAULT_DELEGATION_DEPTH, ge=0, le=20)
    # "worktree" runs each task on its own branch; "checkout" edits the project folder.
    workspaceIsolation: Literal["worktree", "checkout"] = "worktree"
    blockGitPush: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    packageFiles: dict[str, str] = Field(default_factory=dict)
    remSecondBrain: dict[str, Any] = Field(default_factory=dict)
    remReportTheme: dict[str, Any] = Field(default_factory=dict)
    executionTargets: list[ExecutionTarget] = Field(default_factory=list, max_length=100)
    milestones: list[Milestone] = Field(default_factory=list, max_length=500)
    epics: list[Epic] = Field(default_factory=list, max_length=2000)
    # Ticket keys read PREFIX-12. Empty uses letters from the organization slug.
    ticketPrefix: str = Field(default="", pattern=r"^([A-Z][A-Z0-9]{0,7})?$")
    # Mirror knowledge entries as Markdown into the organization's Second Brain folder.
    knowledgeToSecondBrain: bool = False
    # A Git repository Raticode keeps for the whole organization: shared notes, decisions and
    # files every project can use. Runs change it on their own branch; accepting merges it.
    sharedRepository: bool = False
    # When workers may work. Outside these hours nothing starts, and running work stops or
    # finishes as chosen. Defaults to always, which is how organizations worked before.
    operatingHours: OperatingHours = Field(default_factory=OperatingHours)
    # Keep running after Raticode restarts instead of pausing. Interrupted tickets still
    # wait for review either way.
    resumeAfterRestart: bool = False

    @model_validator(mode="after")
    def validate_graph(self) -> OrganizationConfig:
        ids = {employee.id for employee in self.employees}
        if len(ids) != len(self.employees):
            raise ValueError("Employee IDs must be unique")
        by_id = {employee.id: employee for employee in self.employees}
        if len({target.id for target in self.executionTargets}) != len(self.executionTargets):
            raise ValueError("Execution target IDs must be unique")
        for target in self.executionTargets:
            if set(target.employees) - ids:
                raise ValueError("Execution target references an unknown employee")
            if target.secretRef and any(
                target.secretRef not in by_id[eid].secretRefs for eid in target.employees
            ):
                raise ValueError("Remote target credentials must be granted to its employees")
        for employee in self.employees:
            seen = {employee.id}
            manager = employee.reportsTo
            while manager:
                if manager not in ids:
                    raise ValueError(f"Unknown manager: {manager}")
                if manager in seen:
                    raise ValueError("Reporting lines must not contain cycles")
                seen.add(manager)
                manager = by_id[manager].reportsTo
        if not self.name.strip():
            raise ValueError("Organization name is required")
        for collection, reference in ((self.projects, "owner"), (self.teams, "manager")):
            names = [item.get("id") for item in collection]
            if any(
                not isinstance(name, str)
                or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}", name)
                for name in names
            ) or len(set(names)) != len(names):
                raise ValueError("Initiatives and teams need unique portable IDs")
            for item in collection:
                if item.get(reference) and item[reference] not in ids:
                    raise ValueError(f"Unknown {reference}: {item[reference]}")
        for initiative in self.projects:
            budget = initiative.get("monthlyBudgetUsd", 0)
            limit = initiative.get("monthlyTurnLimit", 0)
            tokens = initiative.get("monthlyTokenLimit", 0)
            if type(budget) not in {int, float} or not math.isfinite(budget) or budget < 0:
                raise ValueError("Initiative dollar budgets must be finite nonnegative numbers")
            if type(limit) is not int or not 0 <= limit <= 1000000:
                raise ValueError("Initiative run limits must be integers between 0 and 1000000")
            if type(tokens) is not int or not 0 <= tokens <= 100_000_000_000:
                raise ValueError("Initiative token limits must be nonnegative integers")
        goals = {g.id: g for g in self.goalRecords}
        if len(goals) != len(self.goalRecords):
            raise ValueError("Goal IDs must be unique")
        for goal in self.goalRecords:
            if goal.owner and goal.owner not in ids:
                raise ValueError("Unknown goal owner")
            seen = {goal.id}
            parent = goal.parentId
            while parent:
                if parent not in goals or parent in seen:
                    raise ValueError("Unknown parent goal or goal cycle")
                seen.add(parent)
                parent = goals[parent].parentId
        for employee in self.employees:
            if employee.workspacePaths is not None and any(
                not Path(path).is_absolute() for path in employee.workspacePaths
            ):
                raise ValueError("Employee workspace grants must be absolute paths")
        validate_plan(self, ids)
        self.remSecondBrain.pop("grantId", None)
        return self


def validate_plan(config: OrganizationConfig, employees: set[str]) -> None:
    """Milestones, epics and project checks must form a consistent plan."""
    projects = {p["id"] for p in config.projects}
    for employee in config.employees:
        if employee.ticketAccess and set(employee.ticketAccess) - projects - {"*"}:
            raise ValueError(f"{employee.name}'s ticket permissions name an unknown project")
    for initiative in config.projects:
        if not isinstance(initiative.get("autoRunChecks", False), bool):
            raise ValueError("autoRunChecks must be true or false")
        checks = initiative.get("checks", [])
        if not isinstance(checks, list) or len(checks) > 20:
            raise ValueError("A project can define up to 20 checks")
        names = set()
        for check in checks:
            if (
                not isinstance(check, dict)
                or set(check) - {"name", "command"}
                or not isinstance(check.get("name"), str)
                or not 1 <= len(check["name"].strip()) <= 100
                or not isinstance(check.get("command"), str)
                or not 1 <= len(check["command"].strip()) <= 1000
            ):
                raise ValueError("Project checks need a name and a command")
            if check["name"] in names:
                raise ValueError("Project check names must be unique")
            names.add(check["name"])
    milestones = {m.id: m for m in config.milestones}
    if len(milestones) != len(config.milestones):
        raise ValueError("Milestone IDs must be unique")
    for milestone in config.milestones:
        if milestone.projectId is not None and milestone.projectId not in projects:
            raise ValueError(f"Milestone {milestone.title} belongs to an unknown project")
        if milestone.parentId is not None:
            parent = milestones.get(milestone.parentId)
            if milestone.projectId is None:
                raise ValueError("Only project milestones can serve an organization milestone")
            if parent is None or parent.projectId is not None:
                raise ValueError(f"Milestone {milestone.title} serves an unknown milestone")
    epic_ids = set()
    for epic in config.epics:
        if epic.id in epic_ids:
            raise ValueError("Epic IDs must be unique")
        epic_ids.add(epic.id)
        if epic.milestoneId is not None:
            home = milestones.get(epic.milestoneId)
            if home is None or home.projectId is None:
                raise ValueError(f"Epic {epic.title} needs a project milestone")
            if epic.projectId not in {None, home.projectId}:
                raise ValueError(f"Epic {epic.title} must belong to its milestone's project")
            epic.projectId = home.projectId
        if epic.projectId is None or epic.projectId not in projects:
            raise ValueError(f"Epic {epic.title} needs a project")
        if epic.owner is not None and epic.owner not in employees:
            raise ValueError(f"Epic {epic.title} has an unknown owner")


def ticket_prefix(config: dict[str, Any]) -> str:
    if config.get("ticketPrefix"):
        return str(config["ticketPrefix"])
    letters = re.sub(r"[^A-Za-z0-9]", "", str(config.get("slug") or config.get("name") or ""))
    prefix = letters[:3].upper()
    return prefix if prefix[:1].isalpha() else "T" + prefix[:2]


def number_tickets(tasks: list[dict[str, Any]]) -> None:
    """Give unnumbered tasks the next ticket numbers in creation order.

    Tasks saved before ticket numbers existed get the same numbers whenever they are
    read, and keep them once they are next written.
    """
    highest = max((t.get("number") or 0 for t in tasks), default=0)
    for task in tasks:
        if not task.get("number"):
            highest += 1
            task["number"] = highest


# Knowledge entries, typed links between work and knowledge, and claims with evidence.
PLAN_RECORDS = ("knowledge", "links", "claims")

TaskStatus = Literal["backlog", "todo", "in_progress", "in_review", "blocked", "done", "cancelled"]


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=100_000)
    assignee: str | None = None
    project: str | None = Field(default=None, description="Initiative ID, not a Raticode project")
    parentId: str | None = None
    goal: str = ""
    status: TaskStatus = "todo"
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    recurring: bool = False
    intervalSeconds: int = Field(default=0, ge=0, le=31_536_000)
    dueAt: str | None = None
    evidence: str = ""
    revision: int = 1
    comments: list[dict[str, Any]] = Field(default_factory=list)
    turns: int = 0
    claim: str | None = None
    approvalRequired: bool = False
    approvedRevision: int | None = None
    workspacePath: str | None = None
    goalId: str | None = None
    dependsOn: list[str] = Field(default_factory=list, max_length=100)
    blockReason: str = ""
    reviewer: str | None = None
    reviewRequired: bool = False
    acceptedRevision: int | None = None
    planRevision: int = 1
    createdAt: str = Field(default_factory=now)
    cancellation: str | None = None
    parentRunId: str | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    workflowContract: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None
    workflowAuthorization: str | None = None
    routineId: str | None = None
    occurrenceId: str | None = None
    reviewRequestedAt: str | None = None
    acceptedAt: str | None = None
    # The task's own git worktree and branch when the organization isolates work.
    worktree: dict[str, str] | None = None
    # The removed worktree's branch, kept so finished work stays inspectable.
    releasedWorktree: dict[str, str] | None = None
    # The ticket's worktree of the organization's shared repository, while it is open.
    sharedWorktree: dict[str, str] | None = None
    # A heartbeat check-in Raticode created, not work someone planned.
    heartbeat: bool = False
    # When a worker, you or Rem asked to work on this next ("move to In progress").
    pulledAt: str | None = None
    # Ticket fields: the epic this belongs to, its number in PREFIX-n, what done means,
    # and knowledge entries the plan links to.
    epicId: str | None = None
    number: int | None = Field(default=None, ge=1)
    doneWhen: str = Field(default="", max_length=1000)
    knowledge: list[str] = Field(default_factory=list, max_length=50)


class OrganizationStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "organizations.sqlite3"
        self._anchor_heads: dict[str, int] = {}
        data_dir.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS organizations (
                    id TEXT PRIMARY KEY, project TEXT NOT NULL, revision INTEGER NOT NULL,
                    config TEXT NOT NULL, runtime TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS organization_revisions (
                    organization TEXT NOT NULL, revision INTEGER NOT NULL, at TEXT NOT NULL,
                    actor TEXT NOT NULL, reason TEXT NOT NULL, config TEXT NOT NULL,
                    digest TEXT NOT NULL, previous_digest TEXT NOT NULL,
                    restored_from INTEGER, PRIMARY KEY(organization, revision));
                CREATE TRIGGER IF NOT EXISTS revisions_no_update
                    BEFORE UPDATE ON organization_revisions BEGIN
                    SELECT RAISE(ABORT, 'Organization history is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS revisions_no_delete
                    BEFORE DELETE ON organization_revisions BEGIN
                    SELECT RAISE(ABORT, 'Organization history is append-only'); END;
                CREATE TABLE IF NOT EXISTS organization_projects (
                    identity TEXT PRIMARY KEY, root TEXT NOT NULL, organization TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS organization_tasks (
                    organization TEXT NOT NULL, id TEXT NOT NULL, status TEXT NOT NULL,
                    assignee TEXT, project TEXT, body TEXT NOT NULL,
                    PRIMARY KEY(organization, id));
                CREATE INDEX IF NOT EXISTS org_tasks_status
                    ON organization_tasks(organization, status, assignee);
                CREATE TABLE IF NOT EXISTS organization_comments (
                    organization TEXT NOT NULL, task TEXT NOT NULL, position INTEGER NOT NULL,
                    body TEXT NOT NULL, PRIMARY KEY(organization, task, position));
                CREATE TABLE IF NOT EXISTS organization_records (
                    organization TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
                    body TEXT NOT NULL, PRIMARY KEY(organization, kind, id));
                CREATE TABLE IF NOT EXISTS organization_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, organization TEXT NOT NULL,
                    at TEXT NOT NULL, actor TEXT NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS org_events_index
                    ON organization_events(organization, sequence);
                CREATE TRIGGER IF NOT EXISTS events_no_update
                    BEFORE UPDATE ON organization_events BEGIN
                    SELECT RAISE(ABORT, 'Organization activity is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS events_no_delete
                    BEFORE DELETE ON organization_events BEGIN
                    SELECT RAISE(ABORT, 'Organization activity is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS comments_no_update
                    BEFORE UPDATE ON organization_comments BEGIN
                    SELECT RAISE(ABORT, 'Task conversations are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS comments_no_delete
                    BEFORE DELETE ON organization_comments BEGIN
                    SELECT RAISE(ABORT, 'Task conversations are append-only'); END;
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(organization_events)")}
            # Events written before the chain existed keep NULL digests and are reported as such.
            for column in ("digest", "previous_digest"):
                if column not in columns:
                    db.execute(f"ALTER TABLE organization_events ADD COLUMN {column} TEXT")

        self._migrate_project_ownership()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT id, runtime FROM organizations").fetchall():
                runtime = json.loads(row["runtime"])
                if "tasks" in runtime:
                    self._write_runtime(db, row["id"], runtime)

    def _migrate_project_ownership(self) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT * FROM organizations ORDER BY rowid").fetchall():
                config = json.loads(row["config"])
                if "projectRoots" in config:
                    continue
                roots = []
                conflicts = []
                for path in [
                    row["project"],
                    *[p.get("workspacePath") for p in config.get("projects", [])],
                ]:
                    if not path:
                        continue
                    identity, root = project_identity(path)
                    owner = db.execute(
                        "SELECT organization FROM organization_projects WHERE identity=?",
                        (identity,),
                    ).fetchone()
                    if owner and owner["organization"] != row["id"]:
                        conflicts.append(root)
                        continue
                    db.execute(
                        "INSERT OR IGNORE INTO organization_projects VALUES (?, ?, ?)",
                        (identity, root, row["id"]),
                    )
                    if root not in roots:
                        roots.append(root)
                config["projectRoots"] = roots
                owned = {project_identity(root)[0] for root in roots}
                for initiative in config.get("projects", []):
                    path = initiative.get("workspacePath")
                    if path and project_identity(path)[0] not in owned:
                        initiative.pop("workspacePath")
                reason = "Migrated organization to global project ownership"
                if conflicts:
                    reason += ". Already owned elsewhere: " + ", ".join(conflicts)
                    runtime = json.loads(row["runtime"])
                    runtime["state"] = "paused"
                    runtime["pauseReason"] = (
                        "Project ownership needs review. Some previous project folders belong "
                        "to another organization. Review Projects and configuration history."
                    )
                    db.execute(
                        "UPDATE organizations SET runtime=? WHERE id=?",
                        (json.dumps(runtime), row["id"]),
                    )
                self._revision(db, row["id"], config, "system", reason[:4000], None)

    def _warm_project_ownership(self, config: dict[str, Any]) -> None:
        with self.connect() as db:
            roots = [row[0] for row in db.execute("SELECT root FROM organization_projects")]
        roots.extend(config.get("projectRoots", []))
        roots.extend(
            p["workspacePath"] for p in config.get("projects", []) if p.get("workspacePath")
        )
        for root in set(roots):
            project_identity(root)

    def _set_project_ownership(
        self, db: sqlite3.Connection, oid: str, config: dict[str, Any]
    ) -> None:
        projects = {}
        for value in config["projectRoots"]:
            if not Path(value).is_absolute():
                raise ValueError("Project folders must be absolute paths")
            identity, root = project_identity(value)
            projects[identity] = root
        # A folder may become a Git repository after it was assigned. Recheck stored
        # roots so newly created worktrees cannot be claimed by another organization.
        for owner in db.execute(
            "SELECT p.identity, p.root, o.config FROM organization_projects p "
            "JOIN organizations o ON o.id=p.organization WHERE o.id != ?",
            (oid,),
        ).fetchall():
            identity, _ = project_identity(owner["root"])
            if identity in projects or owner["identity"] in projects:
                name = json.loads(owner["config"])["name"]
                raise OrganizationConflict(
                    f"Project {owner['root']} is already owned by {name}. "
                    "Remove it there before assigning it here."
                )
        for initiative in config["projects"]:
            path = initiative.get("workspacePath")
            if path and not Path(path).is_absolute():
                raise ValueError("Initiative workspace must be an absolute path")
            if path and project_identity(path)[0] not in projects:
                raise ValueError(
                    "Initiative workspace must belong to a project owned by this organization"
                )
            if path:
                initiative["workspacePath"] = str(Path(path).resolve())
        db.execute("DELETE FROM organization_projects WHERE organization=?", (oid,))
        db.executemany(
            "INSERT INTO organization_projects VALUES (?, ?, ?)",
            [(identity, root, oid) for identity, root in projects.items()],
        )
        config["projectRoots"] = list(projects.values())

    def owns_project(self, oid: str, path: str) -> bool:
        identity, _ = project_identity(path)
        with self.connect() as db:
            roots = db.execute(
                "SELECT identity, root FROM organization_projects WHERE organization=?", (oid,)
            ).fetchall()
        return any(identity in {row["identity"], project_identity(row["root"])[0]} for row in roots)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def list_organizations(self, project: str = "") -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM organizations ORDER BY rowid").fetchall()
        return [self.get("", row["id"]) for row in rows]

    def summaries(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT o.id, o.revision, o.config, o.runtime, "
                "(SELECT COUNT(*) FROM organization_tasks t WHERE t.organization=o.id "
                "AND t.status NOT IN ('done','cancelled')) AS open_tasks "
                "FROM organizations o ORDER BY o.rowid"
            ).fetchall()
        result = []
        for row in rows:
            config = json.loads(row["config"])
            runtime = json.loads(row["runtime"])
            result.append(
                {
                    "id": row["id"],
                    "name": config["name"],
                    "revision": row["revision"],
                    "state": runtime["state"],
                    "pauseReason": runtime.get("pauseReason", ""),
                    "archived": runtime.get("archived") is True,
                    "employees": len(config["employees"]),
                    "projectRoots": config.get("projectRoots", []),
                    "openTasks": row["open_tasks"],
                }
            )
        return result

    @staticmethod
    def decode(row: sqlite3.Row) -> dict[str, Any]:
        config = json.loads(row["config"])
        roots = config.get("projectRoots", [row["project"]])
        return {
            "id": row["id"],
            "projectRoot": roots[0] if roots else "",
            "revision": row["revision"],
            "config": config,
            "runtime": json.loads(row["runtime"]),
        }

    def get(self, project: str, oid: str) -> dict[str, Any]:
        with self.connect() as db:
            return self._get(db, project, oid)

    def _get(self, db: sqlite3.Connection, project: str, oid: str) -> dict[str, Any]:
        row = db.execute("SELECT * FROM organizations WHERE id=?", (oid,)).fetchone()
        if row is None:
            raise ValueError("Organization not found")
        org = self.decode(row)
        org["runtime"]["tasks"] = []
        comments: dict[str, list[Any]] = {}
        for comment in db.execute(
            "SELECT task, body FROM organization_comments WHERE organization=? ORDER "
            "BY task, position",
            (oid,),
        ):
            comments.setdefault(comment["task"], []).append(json.loads(comment["body"]))
        for item in db.execute(
            "SELECT body FROM organization_tasks WHERE organization=? ORDER BY rowid", (oid,)
        ):
            task = Task.model_validate(json.loads(item["body"])).model_dump()
            task["comments"] = comments.get(task["id"], [])
            org["runtime"]["tasks"].append(task)
        number_tickets(org["runtime"]["tasks"])
        for kind in (
            "attempts",
            "requests",
            "routines",
            "memories",
            "secrets",
            "occurrences",
            "experiments",
            "usage",
            "webhooks",
            "webhookReceipts",
            *PLAN_RECORDS,
        ):
            org["runtime"][kind] = {
                item["id"]: json.loads(item["body"])
                for item in db.execute(
                    "SELECT id, body FROM organization_records WHERE organization=? AND kind=?",
                    (oid, kind),
                )
            }
        return org

    @staticmethod
    def _write_runtime(db: sqlite3.Connection, oid: str, runtime: dict[str, Any]) -> None:
        for task in runtime.get("tasks", []):
            task.update(Task.model_validate(task).model_dump())
            body = json.dumps({k: v for k, v in task.items() if k != "comments"}, sort_keys=True)
            db.execute(
                "INSERT INTO organization_tasks VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(organization,id) DO UPDATE SET status=excluded.status, "
                "assignee=excluded.assignee, project=excluded.project, "
                "body=excluded.body WHERE body != excluded.body",
                (oid, task["id"], task["status"], task["assignee"], task["project"], body),
            )
            count = db.execute(
                "SELECT COUNT(*) FROM organization_comments WHERE organization=? AND task=?",
                (oid, task["id"]),
            ).fetchone()[0]
            db.executemany(
                "INSERT INTO organization_comments VALUES (?, ?, ?, ?)",
                [
                    (oid, task["id"], i, json.dumps(c))
                    for i, c in enumerate(task["comments"])
                    if i >= count
                ],
            )
        collections = {
            "tasks",
            "attempts",
            "requests",
            "routines",
            "memories",
            "secrets",
            "occurrences",
            "experiments",
            "usage",
            "webhooks",
            "webhookReceipts",
            *PLAN_RECORDS,
        }
        number_tickets(runtime.get("tasks", []))
        for kind in collections - {"tasks"}:
            for key, value in runtime.get(kind, {}).items():
                db.execute(
                    "INSERT INTO organization_records VALUES (?, ?, ?, ?) ON "
                    "CONFLICT(organization,kind,id) DO UPDATE SET body=excluded.body "
                    "WHERE body != excluded.body",
                    (oid, kind, key, json.dumps(value, sort_keys=True)),
                )
        # Idempotency records are the only kind that expires; other records are permanent.
        if "requests" in runtime:
            kept = list(runtime["requests"])
            db.execute(
                "DELETE FROM organization_records WHERE organization=? AND kind='requests' "
                f"AND id NOT IN ({','.join('?' * len(kept))})",
                (oid, *kept),
            )
        body = json.dumps({k: v for k, v in runtime.items() if k not in collections})
        db.execute(
            "UPDATE organizations SET runtime=? WHERE id=? AND runtime != ?", (body, oid, body)
        )

    def create(
        self,
        project: str,
        config: dict[str, Any],
        actor: str,
        reason: str,
        tasks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        config = {"projectRoots": [project] if project else [], **config}
        validated = OrganizationConfig.model_validate(config).model_dump()
        check_heartbeats([], validated["employees"])
        self._warm_project_ownership(validated)
        oid = str(uuid4())
        work = [Task.model_validate(t).model_dump() for t in tasks or []]
        for task in work:
            self.validate_task(task, validated, work)
        runtime = {"state": "paused", "tasks": work, "heartbeats": {}, "generation": 0}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO organizations VALUES (?, ?, 0, ?, ?)",
                (oid, project, "{}", json.dumps(runtime)),
            )
            self._write_runtime(db, oid, runtime)
            self._set_project_ownership(db, oid, validated)
            self._revision(db, oid, validated, actor, reason, None)
        return self.get(project, oid)

    def configure(
        self,
        project: str,
        oid: str,
        config: dict[str, Any],
        expected: int,
        actor: str,
        reason: str,
        restored_from: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        validated = OrganizationConfig.model_validate(config).model_dump()
        self._warm_project_ownership(validated)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._get(db, project, oid)
            if current["runtime"].get("archived"):
                raise ValueError("Restore this archived organization before changing it")
            if restored_from is None:
                check_heartbeats(current["config"]["employees"], validated["employees"])
            if type(expected) is not int or current["revision"] != expected:
                raise OrganizationConflict(
                    "Configuration changed. Reload before saving or restoring."
                )
            if "projectRoots" not in config:
                validated["projectRoots"] = current["config"].get("projectRoots", [])
            self._set_project_ownership(db, oid, validated)
            if dry_run:
                # Validation only, so a request that would fail is refused before it waits.
                db.rollback()
                return current
            if "packageFiles" not in config:
                validated["packageFiles"] = current["config"].get("packageFiles", {})
            runtime = current["runtime"]
            old_employees = {e["id"]: e for e in current["config"]["employees"]}
            new_employees = {e["id"]: e for e in validated["employees"]}
            permissions = {"workspacePaths", "resources", "permissionMode", "paused", "secretRefs"}
            affected = {
                eid
                for eid, previous in old_employees.items()
                if eid not in new_employees
                or any(previous.get(key) != new_employees[eid].get(key) for key in permissions)
            }
            disruptive = restored_from is not None or any(
                current["config"].get(key) != validated.get(key)
                for key in ("projectRoots", "remSecondBrain", "executionTargets")
            )
            disruptive = disruptive or {
                p["id"]: p.get("workspacePath")
                for p in current["config"]["projects"]
                if p.get("workspacePath")
            } != {
                p["id"]: p.get("workspacePath")
                for p in validated["projects"]
                if p.get("workspacePath")
            }
            if disruptive:
                runtime.update(
                    state="paused",
                    generation=runtime["generation"] + 1,
                    pauseReason=(
                        "Paused because a configuration was restored"
                        if restored_from is not None
                        else "Paused because project folders, destinations or Second Brain "
                        "settings changed. Review, then start again."
                    ),
                )
            for task in runtime["tasks"]:
                orphaned = task["status"] not in {"done", "cancelled"} and (
                    (
                        task["assignee"]
                        and task["assignee"] not in {e["id"] for e in validated["employees"]}
                    )
                    or (
                        task["project"]
                        and task["project"] not in {p["id"] for p in validated["projects"]}
                    )
                    or (
                        task.get("goalId")
                        and task["goalId"] not in {g["id"] for g in validated["goalRecords"]}
                    )
                    or (task.get("reviewer") and task["reviewer"] not in new_employees)
                )
                if (task["claim"] and (disruptive or task["assignee"] in affected)) or orphaned:
                    task.update(claim=None, status="blocked", revision=task["revision"] + 1)
                    task["blockReason"] = (
                        "Its worker, project, goal or reviewer was removed. "
                        "Reassign it, then reopen it."
                        if orphaned
                        else "Stopped because the configuration changed while it ran. "
                        "Review it, then reopen it."
                    )
                    task["comments"].append(
                        {
                            "actor": "system",
                            "at": now(),
                            "body": (
                                "Configuration changed the context or removed an owner/initiative. "
                                "Review effects before retrying."
                            ),
                        }
                    )
            self._write_runtime(db, oid, runtime)
            self._revision(db, oid, validated, actor, reason, restored_from)
        return self.get(project, oid)

    def control(
        self, project: str, oid: str, actor: str, state: str, reason: str = ""
    ) -> dict[str, Any]:
        if state not in {"running", "paused"}:
            raise ValueError("State must be running or paused")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._get(db, project, oid)
            config, runtime = current["config"], current["runtime"]
            if state == "running" and runtime.get("archived"):
                raise ValueError("Restore this archived organization before starting it")
            if state == "running" and not config.get("projectRoots"):
                raise ValueError("Assign a Raticode project before starting employees")
            if state == "paused":
                runtime["generation"] += 1
                runtime["pauseReason"] = reason or "Paused"
            else:
                runtime.pop("pauseReason", None)
            runtime["state"] = state
            self._write_runtime(db, oid, runtime)
            self.event(
                db,
                oid,
                actor,
                "organization_control",
                {"state": state},
            )
            return self._get(db, project, oid)

    def archive(self, oid: str, actor: str, archived: bool) -> dict[str, Any]:
        """Hide a stopped organization and release its projects, keeping all history."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._get(db, "", oid)
            runtime = current["runtime"]
            if bool(runtime.get("archived")) == archived:
                return current
            if archived:
                if runtime["state"] != "paused" or any(t["claim"] for t in runtime["tasks"]):
                    raise ValueError("Pause the organization and wait for running work first")
                db.execute("DELETE FROM organization_projects WHERE organization=?", (oid,))
                runtime.update(archived=True, generation=runtime["generation"] + 1)
                runtime["pauseReason"] = "Archived"
            else:
                config = copy.deepcopy(current["config"])
                self._set_project_ownership(db, oid, config)
                runtime.pop("archived", None)
                runtime["pauseReason"] = "Restored from the archive"
            self._write_runtime(db, oid, runtime)
            self.event(
                db, oid, actor, "organization_archived" if archived else "organization_restored", {}
            )
            return self._get(db, "", oid)

    def _revision(
        self,
        db: sqlite3.Connection,
        oid: str,
        config: dict[str, Any],
        actor: str,
        reason: str,
        restored: int | None,
    ) -> None:
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
            raise ValueError("A change reason between 1 and 4000 characters is required")
        previous = db.execute(
            "SELECT revision, digest FROM organization_revisions "
            "WHERE organization=? ORDER BY revision DESC LIMIT 1",
            (oid,),
        ).fetchone()
        revision = previous["revision"] + 1 if previous else 1
        previous_digest = previous["digest"] if previous else ""
        encoded = json.dumps(config, sort_keys=True, ensure_ascii=False)
        at = now()
        digest = hashlib.sha256(
            json.dumps(
                [previous_digest, oid, revision, at, actor, reason, encoded, restored]
            ).encode()
        ).hexdigest()
        db.execute(
            "INSERT INTO organization_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (oid, revision, at, actor, reason, encoded, digest, previous_digest, restored),
        )
        db.execute(
            "UPDATE organizations SET revision=?, config=? WHERE id=?", (revision, encoded, oid)
        )
        self.event(
            db,
            oid,
            actor,
            "configuration_restored" if restored else "configuration_saved",
            {"revision": revision, "reason": reason, "restoredFrom": restored},
        )

    def history(self, project: str, oid: str) -> list[dict[str, Any]]:
        self.get(project, oid)
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM organization_revisions WHERE organization=? ORDER BY revision DESC",
                (oid,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            digest = hashlib.sha256(
                json.dumps(
                    [
                        item["previous_digest"],
                        oid,
                        item["revision"],
                        item["at"],
                        item["actor"],
                        item["reason"],
                        item["config"],
                        item["restored_from"],
                    ]
                ).encode()
            ).hexdigest()
            if digest != item["digest"]:
                raise ValueError("Configuration history integrity check failed")
            item["config"] = json.loads(item["config"])
            result.append(item)
        for index, item in enumerate(result):
            previous = result[index + 1]["digest"] if index + 1 < len(result) else ""
            if item["previous_digest"] != previous:
                raise ValueError("Configuration history chain is incomplete")
        return result

    def restore(
        self, project: str, oid: str, revision: int, expected: int, actor: str, reason: str
    ) -> dict[str, Any]:
        item = next((r for r in self.history(project, oid) if r["revision"] == revision), None)
        if not item:
            raise ValueError("Revision not found")
        return self.configure(project, oid, item["config"], expected, actor, reason, revision)

    def mutate(
        self,
        project: str,
        oid: str,
        actor: str,
        kind: str,
        callback: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            organization = self._get(db, project, oid)
            before = json.dumps(organization["runtime"])
            statuses = {t["id"]: t["status"] for t in organization["runtime"]["tasks"]}
            payload = callback(organization)
            if before == json.dumps(organization["runtime"]) and not payload:
                return organization
            self._write_runtime(db, oid, organization["runtime"])
            # Every state change leaves an event, including scheduler-only updates.
            changes = [
                {
                    "taskId": t["id"],
                    "title": t["title"][:200],
                    "from": statuses.get(t["id"]),
                    "to": t["status"],
                }
                for t in organization["runtime"]["tasks"]
                if statuses.get(t["id"]) != t["status"]
            ]
            if not payload:
                kind, payload = "state_updated", {"source": kind}
            if changes and "taskChanges" not in payload:
                payload = {**payload, "taskChanges": changes}
            self.event(db, oid, actor, kind, payload)
        return organization

    @staticmethod
    def event(
        db: sqlite3.Connection, oid: str, actor: str, kind: str, payload: dict[str, Any]
    ) -> None:
        """Append an event linked to the organization's previous event by hash."""
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        previous = db.execute(
            "SELECT digest FROM organization_events WHERE organization=? AND digest IS NOT NULL "
            "ORDER BY sequence DESC LIMIT 1",
            (oid,),
        ).fetchone()
        previous_digest = previous[0] if previous else ""
        at, body = now(), json.dumps(payload)
        db.execute(
            "INSERT INTO organization_events"
            "(organization, at, actor, kind, payload, digest, previous_digest) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                oid,
                at,
                actor,
                kind,
                body,
                event_digest(previous_digest, oid, at, actor, kind, body),
                previous_digest,
            ),
        )

    @property
    def anchor_path(self) -> Path:
        # Outside the database, so rebuilding or truncating the database can be noticed.
        return self.path.parent / "organization-audit-anchors.jsonl"

    def _anchors(self, oid: str) -> list[dict[str, Any]]:
        try:
            lines = self.anchor_path.read_text().splitlines()
        except OSError:
            return []
        anchors = []
        for line in lines:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and item.get("organization") == oid:
                anchors.append(item)
        return anchors

    def anchor(self, oid: str) -> dict[str, Any] | None:
        """Append the organization's latest event hash to the anchor file if it moved."""
        with self.connect() as db:
            head = db.execute(
                "SELECT sequence, digest FROM organization_events WHERE organization=? "
                "AND digest IS NOT NULL ORDER BY sequence DESC LIMIT 1",
                (oid,),
            ).fetchone()
        if head is None:
            return None
        cached = self._anchor_heads.get(oid)
        if cached is None:
            known = self._anchors(oid)
            cached = known[-1]["sequence"] if known else 0
        if cached == head["sequence"]:
            self._anchor_heads[oid] = cached
            return None
        item = {
            "organization": oid,
            "sequence": head["sequence"],
            "digest": head["digest"],
            "at": now(),
        }
        descriptor = os.open(self.anchor_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "a") as handle:
            handle.write(json.dumps(item) + "\n")
        self._anchor_heads[oid] = head["sequence"]
        return item

    def verify_audit(self, oid: str) -> dict[str, Any]:
        """Check the configuration history chain and the activity chain end to end."""
        try:
            revisions = len(self.history("", oid))
            history: dict[str, Any] = {"ok": True, "revisions": revisions}
        except ValueError as exc:
            history = {"ok": False, "error": str(exc)}
        with self.connect() as db:
            rows = db.execute(
                "SELECT sequence, at, actor, kind, payload, digest, previous_digest "
                "FROM organization_events WHERE organization=? ORDER BY sequence",
                (oid,),
            ).fetchall()
        legacy, chained, expected, broken = 0, 0, "", None
        for row in rows:
            if row["digest"] is None:
                if chained:
                    broken = {
                        "sequence": row["sequence"],
                        "reason": "Unlinked event after the chain began",
                    }
                    break
                legacy += 1
                continue
            digest = event_digest(
                row["previous_digest"] or "",
                oid,
                row["at"],
                row["actor"],
                row["kind"],
                row["payload"],
            )
            if (row["previous_digest"] or "") != expected or digest != row["digest"]:
                broken = {"sequence": row["sequence"], "reason": "Event content or order changed"}
                break
            expected, chained = row["digest"], chained + 1
        digests = {row["sequence"]: row["digest"] for row in rows}
        newest = max(digests, default=0)
        anchors = self._anchors(oid)
        mismatch = next(
            (
                a
                for a in anchors
                if a.get("sequence", 0) > newest
                or digests.get(a.get("sequence")) != a.get("digest")
            ),
            None,
        )
        anchored = {
            "ok": mismatch is None,
            "anchors": len(anchors),
            **(
                {
                    "brokenAt": {
                        "sequence": mismatch["sequence"],
                        "reason": "Entries recorded by the anchor file are missing or changed",
                    }
                }
                if mismatch
                else {}
            ),
        }
        return {
            "history": history,
            "anchors": anchored,
            "activity": {
                "ok": broken is None,
                "events": len(rows),
                "linked": chained,
                "beforeLinking": legacy,
                **({"brokenAt": broken} if broken else {}),
            },
            "ok": history["ok"] and broken is None and mismatch is None,
            "checkedAt": now(),
        }

    def events(
        self, project: str, oid: str, after: int = 0, recent: int = 0
    ) -> list[dict[str, Any]]:
        """Activity after a cursor, or the latest `recent` entries a person would read:
        no run transcripts, restart recoveries or internal checkpoints."""
        self.get(project, oid)
        with self.connect() as db:
            if recent:
                rows = db.execute(
                    "SELECT * FROM (SELECT * FROM organization_events WHERE organization=? "
                    "AND kind NOT IN ('turn_event', 'runtime_recovered', 'consent_checked', "
                    "'check_started', 'memory_check') ORDER BY sequence DESC LIMIT ?) "
                    "ORDER BY sequence",
                    (oid, min(max(recent, 1), 500)),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM organization_events WHERE organization=? "
                    "AND sequence>? ORDER BY sequence LIMIT 500",
                    (oid, after),
                ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def turn_events(self, oid: str, turn: str, limit: int = 2000) -> list[dict[str, Any]]:
        """Stored provider events of one employee run, oldest first."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT sequence, at, payload FROM organization_events WHERE organization=? "
                "AND kind='turn_event' AND json_extract(payload, '$.turnId')=? "
                "ORDER BY sequence LIMIT ?",
                (oid, turn, limit),
            ).fetchall()
        return [
            {"sequence": row["sequence"], "at": row["at"], **json.loads(row["payload"])["event"]}
            for row in rows
        ]

    @staticmethod
    def validate_task(
        task: dict[str, Any], config: dict[str, Any], tasks: list[dict[str, Any]]
    ) -> None:
        if (
            task["approvalRequired"]
            and task["status"] in {"in_progress", "done"}
            and task["approvedRevision"] is None
        ):
            raise ValueError("This task needs approval of its current plan")
        if (
            task.get("reviewRequired")
            and task["status"] == "done"
            and task.get("acceptedRevision") is None
        ):
            raise ValueError("An independent reviewer must accept this result")
        if task.get("reviewer") and (
            task["reviewer"] == task["assignee"]
            or task["reviewer"] not in {e["id"] for e in config["employees"]}
        ):
            raise ValueError("Reviewer must be a different, existing employee")
        if task.get("goalId") and task["goalId"] not in {
            g["id"] for g in config.get("goalRecords", [])
        }:
            raise ValueError("Unknown goal")
        if task.get("epicId"):
            epic = next((e for e in config.get("epics", []) if e["id"] == task["epicId"]), None)
            if epic is None:
                raise ValueError("Unknown epic")
            # A ticket counts against its epic's project budget, so they always agree.
            if task.get("project") not in {None, epic["projectId"]}:
                raise ValueError("A ticket's project must match its epic's project")
            task["project"] = epic["projectId"]
        graph = {t["id"]: t.get("dependsOn", []) for t in tasks}
        graph[task["id"]] = task.get("dependsOn", [])

        pending = [(task["id"], False)]
        visiting: set[str] = set()
        visited: set[str] = set()
        while pending:
            tid, leaving = pending.pop()
            if leaving:
                visiting.remove(tid)
                visited.add(tid)
                continue
            if tid in visited:
                continue
            if tid in visiting or tid not in graph:
                raise ValueError("Unknown dependency or dependency cycle")
            visiting.add(tid)
            pending.append((tid, True))
            pending.extend((dependency, False) for dependency in graph[tid])
        if task["status"] in {"in_progress", "done"} and set(task.get("dependsOn", [])) - {
            t["id"] for t in tasks if t["status"] == "done" and not t["claim"]
        }:
            raise ValueError("Dependencies must finish before this task can run or complete")
        for field in ("createdAt", "dueAt"):
            if task.get(field) and datetime.fromisoformat(task[field]).tzinfo is None:
                raise ValueError(f"Task {field} needs a timezone")
        if task["status"] == "done" and not task["evidence"].strip():
            raise ValueError("Completed tasks need evidence")
        if task["assignee"] and task["assignee"] not in {e["id"] for e in config["employees"]}:
            raise ValueError("Unknown employee")
        if task["project"] and task["project"] not in {p["id"] for p in config["projects"]}:
            raise ValueError("Unknown organization initiative")
        parents = {t["id"]: t.get("parentId") for t in tasks}
        seen = {task["id"]}
        parent = task.get("parentId")
        while parent:
            if parent not in parents or parent in seen:
                raise ValueError("Unknown parent task or task cycle")
            seen.add(parent)
            parent = parents[parent]
        if task.get("dueAt"):
            timestamp = datetime.fromisoformat(task["dueAt"])
            if timestamp.tzinfo is None:
                raise ValueError("Task dueAt needs a timezone")
        if task["recurring"] and task["intervalSeconds"] and task["intervalSeconds"] < 60:
            raise ValueError("Recurring intervals must be at least 60 seconds")


def config_diff(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    if before == after:
        return []
    if (
        isinstance(before, list)
        and isinstance(after, list)
        and all(isinstance(item, dict) and "id" in item for item in before + after)
    ):
        return config_diff(
            {item["id"]: item for item in before}, {item["id"]: item for item in after}, path
        )
    if isinstance(before, dict) and isinstance(after, dict):
        return [
            change
            for key in sorted(before.keys() | after.keys())
            for change in config_diff(before.get(key), after.get(key), f"{path}/{key}")
        ]
    return [{"path": path or "/", "before": copy.deepcopy(before), "after": copy.deepcopy(after)}]


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")[:100] or "organization"
