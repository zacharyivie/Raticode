"""Validated recipes and predictable local-time schedules for Rem responsibilities."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from gofer.core.provider_capabilities import ProviderId

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


def worktree_skill(resources: dict[str, Any]) -> str | None:
    """Name of an enabled skill that sets the project's own worktree process.

    Such a skill decides where and on which branch changes go, so Rem must not
    put the task in a worktree of its own.
    """
    from gofer.core.rem_library import skill_folder, skill_header

    for skill in resources.get("skills") or []:
        if not isinstance(skill, dict) or skill.get("enabled") is False or not skill.get("path"):
            continue
        folder = skill_folder(str(skill["path"]))
        header = skill_header(folder)
        name = header.get("name") or folder.name
        if "worktree" in f"{name} {header.get('description', '')}".lower():
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
    suggestions: list[Annotated[str, Field(max_length=300)]] = Field(
        default_factory=list, max_length=10
    )

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
    suggestions: list[Annotated[str, Field(max_length=300)]] = Field(
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
