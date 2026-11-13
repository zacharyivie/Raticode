"""Validated recipes and predictable local-time schedules for Rem responsibilities."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from gofer.core.provider_capabilities import ProviderId
from gofer.ui.rem_projects import ProjectOpen

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
ACTIVE = {"queued", "executing", "verifying", "repairing", "cancelling"}
NEEDS_ATTENTION = {"blocked", "failed", "outcome_unknown"}


def now() -> str:
    return datetime.now(UTC).isoformat()


class RemConflict(ValueError):
    pass


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["once", "daily", "weekly", "interval", "cron"] = "once"
    timezone: str = "UTC"
    at: str | None = None
    local_time: str = Field(default="09:00", pattern=r"^([01]?\d|2[0-3]):[0-5]\d$")
    weekdays: list[str] = Field(default_factory=lambda: ["monday"], max_length=7)
    every_minutes: int = Field(default=60, ge=1, le=525600, strict=True)
    cron: str = ""

    @model_validator(mode="after")
    def validate_schedule(self) -> Schedule:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Choose a valid timezone") from exc
        if self.kind == "weekly" and (
            not self.weekdays or any(day not in WEEKDAYS for day in self.weekdays)
        ):
            raise ValueError("Choose valid weekdays")
        if len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError("Weekdays must not repeat")
        if self.at is not None:
            parsed = datetime.fromisoformat(self.at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("A reminder time needs a timezone")
        if self.kind == "cron":
            CronTrigger.from_crontab(self.cron, timezone=ZoneInfo(self.timezone))
        return self

    def next(self, after: datetime, *, initial: bool = False) -> str | None:
        after = after.astimezone(UTC)
        if self.kind == "once":
            return (
                datetime.fromisoformat(self.at.replace("Z", "+00:00")).astimezone(UTC).isoformat()
                if initial and self.at
                else after.isoformat()
                if initial
                else None
            )
        if self.kind == "interval":
            return (after + timedelta(minutes=self.every_minutes)).isoformat()
        zone = ZoneInfo(self.timezone)
        if self.kind == "cron":
            value = CronTrigger.from_crontab(self.cron, timezone=zone).get_next_fire_time(
                None, after + timedelta(seconds=1)
            )
            return value.astimezone(UTC).isoformat() if value else None
        hour, minute = map(int, self.local_time.split(":"))
        local = after.astimezone(zone)
        for offset in range(9):
            day = local.date() + timedelta(days=offset)
            if self.kind == "weekly" and WEEKDAYS[day.weekday()] not in self.weekdays:
                continue
            # fold=0 chooses the first repeated instant. Round-trip advances a
            # nonexistent spring-forward time to the corresponding valid time.
            candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone, fold=0)
            instant = candidate.astimezone(UTC)
            if instant > after:
                return instant.isoformat()
        raise ValueError("Could not calculate next occurrence")


class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=4000)
    authored_by: Literal["user", "rem_recommended", "project_rules"] = "user"


class Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    type: Literal["research", "maintain", "develop", "errand", "reminder"]
    prompt: str = Field(min_length=1, max_length=100000)
    project_dir: str | None = None
    provider: ProviderId | None = None
    model: str | None = Field(default=None, min_length=1, max_length=256)
    effort: str | None = Field(default=None, max_length=256)
    execution_mode: Literal["work", "conversation"] = "work"
    conversation_turns: int = Field(default=1, ge=1, le=12, strict=True)
    schedule: Schedule = Field(default_factory=Schedule)
    acceptance_criteria: list[Criterion] = Field(default_factory=list, max_length=40)
    # The user asked to be sent the file this task finds or makes. Only a run started
    # by that user's chat message attaches it, so scheduled runs never send files.
    send_files: bool = Field(default=False, strict=True)
    # Start condition: task or run IDs this one-time task waits for (rem_task_after).
    # It starts once every one of them has succeeded.
    after: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        default_factory=list, max_length=10
    )
    # Start condition: wait for the Rem runs going when the task is saved to end however
    # they end ("snapshot"), or until no other run is going at all ("idle", a live check).
    after_running: Literal["snapshot", "idle"] | None = None
    # Start condition: wait for the self-work window running now (or the next one) to end.
    after_self_work: bool = Field(default=False, strict=True)
    # With a start condition, a reminder whose prompt goes back to Rem as the user's
    # request when the conditions are met, so Rem can plan work from what they found.
    ask_rem: bool = Field(default=False, strict=True)

    @field_validator("after", mode="before")
    @classmethod
    def default_after(cls, value: Any) -> Any:
        return [] if value is None else value

    @field_validator("model", "effort")
    @classmethod
    def validate_selection(cls, value: str | None) -> str | None:
        if value is not None and (value != value.strip() or any(ord(c) < 32 for c in value)):
            raise ValueError("Use a provider model ID or effort without whitespace or controls")
        return value

    @field_validator("schedule", mode="before")
    @classmethod
    def default_schedule(cls, value: Any) -> Any:
        # Providers commonly serialize optional, unspecified schedules as null.
        # Treat that exactly like an omitted schedule; validate explicit schedules.
        return {} if value is None else value

    @model_validator(mode="after")
    def validate_recipe(self) -> Recipe:
        self.title = self.title.strip()
        self.prompt = self.prompt.strip()
        if not self.title or not self.prompt:
            raise ValueError("Tell Rem what to do")
        if self.type == "maintain" and not self.project_dir:
            raise ValueError("Maintenance work needs a project")
        if self.execution_mode == "conversation" and self.type != "errand":
            raise ValueError("Only discussion tasks can use conversational execution")
        self.after = list(dict.fromkeys(ref.strip() for ref in self.after))
        if any(not ref for ref in self.after):
            raise ValueError("Name each task to wait for by its ID")
        waits = bool(self.after or self.after_running or self.after_self_work)
        if waits and self.schedule.kind != "once":
            raise ValueError("Only a one-time task can wait for another task")
        if self.ask_rem and (self.type != "reminder" or not waits):
            raise ValueError("ask_rem needs a reminder that waits for another task")
        if any(not c.description.strip() for c in self.acceptance_criteria):
            raise ValueError("Acceptance criteria must not be blank")
        ids = [c.id for c in self.acceptance_criteria]
        if len(set(ids)) != len(ids):
            raise ValueError("Acceptance criterion IDs must be unique")
        if not self.acceptance_criteria:
            descriptions = (
                ["Complete the requested conversation and report back without inventing facts"]
                if self.execution_mode == "conversation"
                else {
                    "research": [
                        "Answer the requested question with cited sources and stated limits",
                        "Save a readable report to the authorized destination",
                    ],
                    "maintain": [
                        "Resolve the requested maintenance issue within the project scope",
                        "Run relevant regression tests and required project checks",
                    ],
                    "develop": [
                        "Implement the requested behavior and demonstrate it works",
                        "Pass relevant regression tests and required project checks",
                    ],
                    "errand": [
                        "Confirm the requested action through its receipt and resulting state"
                    ],
                    "reminder": ["Deliver this reminder once at the requested time"],
                }[self.type]
            )
            self.acceptance_criteria = [
                Criterion(description=text, authored_by="rem_recommended") for text in descriptions
            ]
        return self


def read_only_recipe(recipe: dict[str, Any]) -> bool:
    """Research and conversations run with read-only workers and never edit a project."""
    return recipe.get("type") == "research" or recipe.get("execution_mode") == "conversation"


def worker_config(config: dict[str, Any], recipe: dict[str, Any]) -> dict[str, Any]:
    """Apply task selections without changing Rem's settings or resource grants."""
    selected = dict(config)
    provider = recipe.get("provider")
    if provider and provider != selected.get("provider", "codex"):
        selected.update(provider=provider, model="cli-default", effort="")
    if recipe.get("model") is not None:
        selected.update(model=recipe["model"], effort="")
    if recipe.get("effort") is not None:
        selected["effort"] = recipe["effort"]
    return selected


def chat_config(config: dict[str, Any]) -> dict[str, Any]:
    """The selection Rem uses for its own chat replies.

    Settings > Rem can give the chat a faster provider, model or effort than
    tasks and threads use. Without a chat provider, the chat follows them.
    """
    provider = config.get("chat_provider")
    if not provider:
        return config
    return {
        **config,
        "provider": provider,
        "model": config.get("chat_model") or "cli-default",
        "effort": config.get("chat_effort") or "",
    }


def worker_resources(config: dict[str, Any], *, readonly: bool, review: bool) -> dict[str, Any]:
    """The thread tools a Rem task receives: the user's thread defaults.

    Desktop threads start from the same defaults (Settings > Thread tools), so
    a writer task gets the identical selection. Read-only work keeps shell and
    the enabled skills, which are instructions it only reads. MCP servers,
    subagents and the browser can change things elsewhere, so they stay off.
    Research may search the web when the user allows it; reviews never do.
    """
    selected = config.get("resources") or {}
    resources = dict(selected)
    resources["shell"] = bool(resources.get("shell", True))
    resources["web"] = bool(resources.get("web", False))
    if readonly:
        resources = {
            "shell": resources["shell"],
            "web": resources["web"] and not review,
            "agents": False,
            "browser": False,
            "skills": [
                skill
                for skill in selected.get("skills") or []
                if isinstance(skill, dict) and skill.get("enabled") is not False
            ],
        }
    return resources


def _worktree_skills(resources: dict[str, Any]) -> list[tuple[str, str]]:
    """Name and description of each enabled skill about the project's worktree process."""
    from gofer.core.rem_library import skill_folder, skill_header

    found = []
    for skill in resources.get("skills") or []:
        if not isinstance(skill, dict) or skill.get("enabled") is False or not skill.get("path"):
            continue
        folder = skill_folder(str(skill["path"]))
        header = skill_header(folder)
        name = header.get("name") or folder.name
        description = header.get("description", "")
        if "worktree" in f"{name} {description}".lower():
            found.append((name, description))
    return found


def worktree_skill(resources: dict[str, Any]) -> str | None:
    """Name of an enabled skill that sets the project's own worktree process.

    Such a skill decides where and on which branch changes go, so Rem must not
    put the task in a worktree of its own.
    """
    skills = _worktree_skills(resources)
    return skills[0][0] if skills else None


# A skill that says its branches end up on a local ``dev`` branch, such as
# "rebases onto dev and fast-forwards dev". A general worktree skill (for example one
# that only creates isolated worktrees) doesn't, so Rem's landing check stays off for it.
_LANDS_ON_DEV = re.compile(
    r"\b(?:fast-forward\w*|merg\w*|land\w*|rebas\w*|integrat\w*)\b[^.\n]{0,60}?\bdev\b",
    re.IGNORECASE,
)


def landing_skill(resources: dict[str, Any]) -> str | None:
    """Name of an enabled worktree skill whose description says changes land on dev.

    Only such a skill turns on the check that a writer task's branch reached local
    ``dev``; a repository that merely has a ``dev`` branch and some worktree skill
    keeps working as before.
    """
    for name, description in _worktree_skills(resources):
        if _LANDS_ON_DEV.search(description):
            return name
    return None


def completion_recap(summary: str) -> str:
    """Keep the result's opening paragraph; details remain in the worker thread."""
    paragraph = summary.strip().split("\n\n", 1)[0]
    if len(paragraph) <= 320:
        return paragraph
    short = paragraph[:317]
    boundary = max(short.rfind(". "), short.rfind("! "), short.rfind("? "))
    if boundary > 40:
        return short[: boundary + 1]
    return short.rsplit(" ", 1)[0] + "…"


# The end of a sentence or line, where a lead can stop without cutting a thought.
LEAD_END = re.compile(r"[.!?](?=\s)|\n")
SUGGESTION_CHARS = 300


def split_lead(text: str, limit: int) -> tuple[str, str]:
    """The longest opening of [text] within [limit] characters, and the rest of it.

    The opening stops after a sentence or line when one ends past a third of the
    limit, otherwise after a word with an ellipsis, which the rest then opens with.
    """
    text = text.strip()
    if len(text) <= limit:
        return text, ""
    ends = [m.end() for m in LEAD_END.finditer(text[: limit + 1]) if m.end() <= limit]
    if ends and ends[-1] > limit // 3:
        return text[: ends[-1]].rstrip(), text[ends[-1] :].strip()
    cut = text[: limit - 1]
    head = cut.rsplit(None, 1)[0] if len(cut.split(None, 1)) > 1 else cut
    return head.rstrip() + "…", "…" + text[len(head) :].strip()


def _max_length(field: Any) -> int | None:
    return next(
        (m.max_length for m in field.metadata if isinstance(getattr(m, "max_length", None), int)),
        None,
    )


def fit_lengths(
    model: type[BaseModel], raw: Any, *, lead: str | None = None, prose: tuple[str, ...] = ()
) -> Any:
    """Shorten over-long prose in a model's JSON reply instead of rejecting all of it.

    A model asked for a short summary sometimes writes a long one. Rejecting the
    whole reply loses its details and suggestions too, so the [lead] field keeps
    what fits, cut after a sentence, and the text cut from it opens details when
    both fit there. Other [prose] fields are cut the same way, and suggestions
    longer than a short line are dropped. Every other field is left to validation.
    """
    if not isinstance(raw, dict):
        return raw
    raw = dict(raw)
    fields = model.model_fields
    keys = {
        name: [name, *[c for c in getattr(fields[name].validation_alias, "choices", []) if c]]
        for name in (*([lead] if lead else []), *prose)
    }
    if lead:
        key = next((k for k in keys[lead] if isinstance(raw.get(k), str)), None)
        limit = _max_length(fields[lead])
        if key and limit and len(raw[key]) > limit:
            raw[key], rest = split_lead(raw[key], limit)
            details = raw.get("details")
            room = _max_length(fields["details"]) if "details" in fields else None
            if rest and room and (details is None or isinstance(details, str)):
                joined = "\n\n".join(filter(None, [rest, (details or "").strip()]))
                if len(joined) <= room or not (details or "").strip():
                    raw["details"] = joined
    for name in prose:
        limit = _max_length(fields[name])
        for key in keys[name]:
            if limit and isinstance(raw.get(key), str) and len(raw[key]) > limit:
                raw[key] = split_lead(raw[key], limit)[0]
    if "suggestions" in fields and isinstance(raw.get("suggestions"), list):
        count = _max_length(fields["suggestions"]) or 10
        raw["suggestions"] = [
            s for s in raw["suggestions"] if not isinstance(s, str) or len(s) <= SUGGESTION_CHARS
        ][:count]
    return raw


def occurrence_context(run: dict[str, Any]) -> dict[str, Any] | None:
    """Where this run sits in a repeated task: its moment and what was already accepted.

    Repeated work, such as a weekly review of new comments, should cover only
    what's new since the last accepted run and compare with it instead of
    starting from nothing. One-off tasks that never succeeded need no context.
    """
    recipe = run["recipe"]
    schedule = recipe.get("schedule") or {}
    previous = run.get("previous_success")
    recurring = schedule.get("kind", "once") != "once"
    if not previous and not recurring:
        return None
    context: dict[str, Any] = {
        "this_run_for": run.get("scheduled_for") or run["created_at"],
        "started_by": "schedule" if run.get("scheduled_for") else "request",
        "schedule_timezone": schedule.get("timezone", "UTC"),
        "previous_accepted_run": previous,
    }
    if previous:
        context["new_since"] = previous["for"]
    return context


OCCURRENCE_GUIDANCE = (
    "\nThis task has run before or repeats on a schedule. Unless the task names its own "
    "period, cover what is new from new_since up to this_run_for (UTC instants). If the "
    "task names a period, use it. Read the previous accepted run's reports when comparing "
    "helps, say what changed since then, and never present earlier findings as new. If "
    "nothing new happened, say so plainly instead of repeating the old result. Without a "
    "previous accepted run, use the task's period or state the default you chose.\n"
    "Occurrence context:\n"
)


class TaskControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=1, max_length=128)
    action: Literal[
        "pause", "resume", "cancel", "delete", "run", "update", "follow_up", "retry_review"
    ]
    patch: dict[str, Any] = Field(default_factory=dict)
    message: str = Field(default="", max_length=20000)

    @model_validator(mode="after")
    def validate_follow_up(self) -> TaskControl:
        if self.action == "follow_up" and not self.message.strip():
            raise ValueError("A follow-up needs a message to the worker")
        if self.action != "follow_up" and self.message:
            raise ValueError("Only a follow-up can send a worker message")
        return self


class WorkerReply(BaseModel):
    """Rem's reply or a concrete request for human authorization."""

    model_config = ConfigDict(extra="forbid")
    next_message: str = Field(
        default="",
        max_length=20000,
        validation_alias=AliasChoices("next_message", "follow_up"),
    )
    needs_user: bool = Field(default=False, strict=True)
    user_question: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def validate_attention(self) -> WorkerReply:
        if self.needs_user and not self.user_question.strip():
            raise ValueError("User attention needs a concrete question")
        return self


class ConversationDecision(WorkerReply):
    """Private completion decision in addition to Rem's conversational reply."""

    criteria: list[dict[str, Any]] = Field(max_length=40)
    summary: str = Field(min_length=1, max_length=1000)

    @field_validator("summary")
    @classmethod
    def nonempty_summary(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A conversation needs a summary")
        return value


class MemoryChange(BaseModel):
    """Correct or forget one saved memory by its exact ID."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64)
    action: Literal["update", "forget"]
    text: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def corrected_text(self) -> MemoryChange:
        if self.action == "update" and not self.text.strip():
            raise ValueError("A memory update needs its corrected text")
        return self


class TicketRequest(BaseModel):
    """A change Rem makes to the global Kanban board because the user asked for it."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["create", "edit", "move", "assign"]
    ticket_id: str | None = Field(default=None, max_length=128)
    scope: Literal["rem", "organization"] | None = None
    organization_id: str | None = Field(default=None, max_length=100)
    to_organization_id: str | None = Field(default=None, max_length=100)
    assignee_id: str | None = Field(default=None, max_length=100)
    expected_revision: int | None = None
    title: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=100000)
    priority: Literal["critical", "high", "medium", "low"] | None = None
    status: Literal["backlog", "todo"] | None = None
    column: Literal["backlog", "todo", "in_progress", "cancelled"] | None = None
    type: Literal["research", "maintain", "develop", "errand"] | None = None
    project_dir: str | None = None
    done_when: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def enough_to_act(self) -> TicketRequest:
        if self.action == "create":
            if not (self.title or "").strip():
                raise ValueError("A new ticket needs a title")
        elif not self.ticket_id or self.expected_revision is None:
            raise ValueError("Changing a ticket needs its exact ticket_id and revision")
        return self

    def label(self) -> str:
        return self.title or self.ticket_id or "a ticket"

    def done_text(self, owner: str, key: str = "") -> str:
        """What the reply says was done. A ticket's key shows in chat as a pill with its title."""
        subject = key or f"“{self.label()}”"
        if self.action == "create":
            column = "Backlog" if self.status == "backlog" else "To do"
            return f"Added {subject} to {owner} {column}."
        return f"Updated {subject} on the board."


class ClockTime(BaseModel):
    """A moment the user named: an exact instant, or a wall-clock time in a time zone.

    ``time`` is 24-hour ``HH:MM``. ``day`` is today, tonight or tomorrow as the user said it,
    ``date`` an explicit local date; with neither, the next time that clock time comes round.
    ``timezone`` is an IANA name; without one, the user's zone (this computer's) is used.
    The backend turns it into an instant and refuses times that passed, don't exist, or
    happen twice on a daylight-saving change.
    """

    model_config = ConfigDict(extra="forbid")
    at: str | None = Field(default=None, max_length=64)
    time: str | None = Field(default=None, pattern=r"^([01]?\d|2[0-3]):[0-5]\d$")
    day: Literal["today", "tonight", "tomorrow"] | None = None
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    timezone: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def one_moment(self) -> ClockTime:
        if bool(self.at) == bool(self.time):
            raise ValueError("Give either an exact instant (at) or a clock time (time)")
        if self.at and (self.day or self.date):
            raise ValueError("An exact instant already has its date")
        if self.day and self.date:
            raise ValueError("Give day or date, not both")
        return self


class SelfWorkRequest(BaseModel):
    """Change Rem's self-work because the user asked: on, off, a timed window, or a report.

    ``enable``/``disable`` flip the Settings > Rem switch. ``schedule`` adds a window: from
    ``start`` (now when absent) until ``end``, or ``until`` the tasks running now finish.
    ``stop_at`` ends the self-work running now at ``end``. ``cancel_schedule`` drops the
    windows. ``report`` posts what self-work did in its current or last period. The thread
    limit, backlog pulling and Rem's projects never change here. The backend applies it and
    writes the result into the reply.
    """

    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "enable", "disable", "status", "schedule", "stop_at", "cancel_schedule", "report"
    ]
    start: ClockTime | None = None
    end: ClockTime | None = None
    until: Literal["running_done"] | None = None
    # What the switch becomes when the window ends: back to what it was, or on or off.
    after_end: Literal["restore", "on", "off"] = "restore"
    # Inside the window, still wait for 15 minutes without a prompt before starting.
    wait_for_idle: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def complete(self) -> SelfWorkRequest:
        if self.action == "schedule" and not (self.end or self.until):
            raise ValueError("A self-work window needs an end time or until")
        if self.action == "schedule" and self.end and self.until:
            raise ValueError("Give an end time or until, not both")
        if self.action == "stop_at" and not self.end:
            raise ValueError("stop_at needs the end time")
        if self.action not in {"schedule", "stop_at"} and (self.start or self.end or self.until):
            raise ValueError("Only schedule and stop_at take times")
        if self.action == "stop_at" and self.start:
            raise ValueError("stop_at takes only an end time")
        return self


class OrganizationApproval(BaseModel):
    """Organizations requests the user, in the message Rem is answering, said to approve.

    ``request_ids`` names them; ``all`` means every request in that organization that was
    waiting when the user sent the message. ``reason`` is Rem's short note on why the
    user's message, read with the conversation, is plainly their okay; it is recorded with
    each approval. Raticode applies them (organization_auto) and writes what happened into
    the reply.
    """

    model_config = ConfigDict(extra="forbid")
    organization_id: str = Field(min_length=1, max_length=200)
    request_ids: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=20
    )
    all: bool = False
    reason: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def names_requests(self) -> OrganizationApproval:
        if bool(self.request_ids) == self.all:
            raise ValueError("Name request_ids, or set all to true, not both")
        return self


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: str = Field(min_length=1, max_length=4000)
    tasks: list[Recipe] = Field(default_factory=list, max_length=5)
    memories: list[str] = Field(default_factory=list, max_length=10)
    memory_changes: list[MemoryChange] = Field(default_factory=list, max_length=10)
    controls: list[TaskControl] = Field(default_factory=list, max_length=5)
    # Absolute paths of existing files the user asked Rem to send.
    files: list[Annotated[str, Field(min_length=1, max_length=4096)]] = Field(
        default_factory=list, max_length=8
    )
    # Optional depth under the short reply, shown behind a "Details" disclosure.
    details: str = Field(default="", max_length=6000)
    # Up to three follow-up prompts. They only fill the message box when tapped.
    suggestions: list[Annotated[str, Field(max_length=SUGGESTION_CHARS)]] = Field(
        default_factory=list, max_length=10
    )
    # Cards for the phone that asked (rem_phone_actions); invalid ones are dropped, not fatal.
    phone_actions: list[Any] = Field(default_factory=list, max_length=10)
    # Local folders this request needs open as projects; Raticode opens or creates them
    # before the decision's tasks start (rem_projects).
    open_projects: list[ProjectOpen] = Field(default_factory=list, max_length=3)
    # Changes to the global Kanban board the user asked for (rem_kanban).
    tickets: list[TicketRequest] = Field(default_factory=list, max_length=10)
    # Turning self-work on or off, or asking whether it is on (rem_self_work).
    self_work: SelfWorkRequest | None = None
    # Organizations requests the user told Rem to approve in this message.
    org_approvals: list[OrganizationApproval] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def suggested_criteria(self) -> Decision:
        for task in self.tasks:
            for criterion in task.acceptance_criteria:
                if "authored_by" not in criterion.model_fields_set:
                    criterion.authored_by = "rem_recommended"
        return self


class TaskUpdate(BaseModel):
    """A conversational outcome, with a specific question only when indispensable."""

    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=700)
    needs_user: bool = Field(default=False, strict=True)
    user_question: str = Field(default="", max_length=500)
    details: str = Field(default="", max_length=6000)
    suggestions: list[Annotated[str, Field(max_length=SUGGESTION_CHARS)]] = Field(
        default_factory=list, max_length=10
    )
    # Absolute paths of files the user asked to be sent; used only for runs that
    # answer the user's own chat request (``deliver_files_to``).
    files: list[Annotated[str, Field(min_length=1, max_length=4096)]] = Field(
        default_factory=list, max_length=8
    )

    @model_validator(mode="after")
    def require_question(self) -> TaskUpdate:
        if not self.summary.strip() or (self.needs_user and not self.user_question.strip()):
            raise ValueError("A task update needs a summary and any required question")
        return self


def checked_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value
