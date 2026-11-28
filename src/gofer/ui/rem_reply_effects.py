"""What one chat reply does to tasks: follow-ups, task controls, new tasks and the reply message.

``RemHome._reply`` builds a ``ReplyEffects`` once Rem's decision is final, after the
request's grants, project opening and cancel check. ``continue_latest`` and
``fold_repeats`` turn a terse "continue" or a repeated one-time task into changes to
existing work. ``apply_controls``, ``start_tasks`` and ``post_reply`` then run inside
``_reply``'s single transaction, after the settings and edit-card effects, so every
effect of one decision commits together or not at all, except a control that fails as
it applies: that one is rolled back alone (a savepoint) and the reply names it.

The class changes the ``Decision`` that ``_reply`` holds, never a copy, and keeps the
run the reply follows in one field, ``followed_run``. It reaches ``RemHome`` only through
the ``home`` it was given, at call time, so tests that replace a method on the instance
still reach this code.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
import unicodedata
from typing import TYPE_CHECKING, Any, cast

from gofer.ui import rem_task_after as after_tasks
from gofer.ui.rem_home_models import (
    ACTIVE,
    Decision,
    Recipe,
    TaskControl,
    TaskUpdate,
    chat_config,
)
from gofer.ui.rem_home_results import clean_suggestions
from gofer.ui.rem_home_store import encode
from gofer.ui.rem_kanban import link_tickets
from gofer.ui.rem_reply_prompt import CONTROL_WORDS
from gofer.utils.terminal_text import one_line

if TYPE_CHECKING:
    from gofer.ui.rem_home import RemHome

# A terse continuation references the latest linked task, even when older thread
# content led the model to propose a duplicate or stop.
CONTINUATION = re.compile(
    r"\s*(?:please\s+)?(?:try(?:\s+(?:that|it))?\s+again|retry(?:\s+the\s+review)?|"
    r"continue(?:\s+working)?(?:\s+on)?(?:\s+(?:that|it|the\s+(?:task|work)))?|"
    r"keep\s+(?:going|working)|resume(?:\s+(?:that|it|the\s+(?:task|work)))?)\s*[.!]?\s*",
    re.IGNORECASE,
)


def title_key(title: str) -> str:
    """Compare task titles without case, quote style, punctuation or spacing noise."""
    text = unicodedata.normalize("NFKC", title).casefold()
    text = re.sub(r"[‘’“”'\"`]", "", text)
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def after_reference(
    ref: str, index: int, created: list[str], fresh: dict[int, int], followed: dict[int, str]
) -> str:
    """A task's ``after`` entry with ``new:N`` (the Nth task Rem listed) made a task ID."""
    match = re.fullmatch(r"new:(\d+)", ref)
    if not match:
        return ref
    listed = int(match[1])
    if listed in followed:
        return followed[listed]
    position = fresh.get(listed)
    if position is None or position >= index:
        raise ValueError("A new task can only wait for a task listed before it")
    return created[position]


def reply_extras(
    decision: Decision | TaskUpdate, chat: dict[str, Any] | None, started: float | None
) -> dict[str, Any]:
    """Details, follow-up suggestions and reply timing stored with a Rem message."""
    extra: dict[str, Any] = {}
    if decision.details.strip():
        extra["details"] = decision.details.strip()
    suggestions = clean_suggestions(decision.suggestions)
    if suggestions:
        extra["suggestions"] = suggestions
    if chat is not None and started is not None:
        extra["meta"] = {
            "provider": str(chat.get("provider") or ""),
            "model": str(chat.get("model") or ""),
            "seconds": round(time.monotonic() - started, 1),
        }
    return extra


def left_out_note(said: list[str]) -> str:
    """The sentence a reply ends with when some of its task changes couldn't apply."""
    if len(said) == 1:
        return (
            f"I couldn't apply one task change, so I left it out: {said[0]}. "
            "Everything else in this reply went ahead."
        )
    return (
        f"I couldn't apply {len(said)} task changes, so I left them out:\n"
        + "\n".join(f"- {item}" for item in said)
        + "\nEverything else in this reply went ahead."
    )


def reply_label(decision: Decision) -> str:
    """What the chat shows Rem doing while it applies a final decision."""
    return (
        "Starting a task"
        if decision.tasks
        else "Updating your tasks"
        if decision.controls
        else "Writing the reply"
    )


class ReplyEffects:
    """The task changes and reply message of one final chat decision."""

    def __init__(
        self,
        home: RemHome,
        decision: Decision,
        known: dict[str, dict[str, Any]],
        pending: Any,
        delegation_context: dict[str, Any],
        started: float,
        dropped_actions: bool,
        *,
        by_user: bool = True,
    ) -> None:
        self.home = home
        # Whether the user's own message, here or on their phone, asked for these changes.
        self.by_user = by_user
        self.decision = decision
        # The tasks as read before the model answered; apply_controls rereads each one.
        self.known = known
        self.pending = pending
        self.delegation_context = delegation_context
        # When the reply began (its timing) and whether a workflow's work was dropped from it.
        self.started = started
        self.dropped_actions = dropped_actions
        # The run this reply follows, whose card the reply message carries.
        self.followed_run: dict[str, Any] | None = None
        # "new:N" in a task's after names the Nth task of this decision. A task
        # that became a follow-up of existing work is named by that task's ID.
        self.fresh_index: dict[int, int] = {}
        self.followed: dict[int, str] = {}

    def continue_latest(self, replying_to: dict[str, Any] | None) -> None:
        """A "continue" or "try again" goes to the task it answers or the newest card's."""
        decision, known = self.decision, self.known
        # A reply to a task's message continues that task, not the newest one.
        replied = ((replying_to or {}).get("work") or {}).get("task_id")
        task = known.get(replied) if replied else None
        if task is None and (latest := self.home.store.latest_card_task(set(known))):
            task = known[latest]
        # A task on hold has no run yet: "retry" means the task it waits for.
        held = (task or {}).get("dependency") or {}
        if task and held.get("state") == "held" and not task.get("last_run"):
            task = next(
                (
                    known[item["task_id"]]
                    for item in held.get("prerequisites") or []
                    if item.get("retryable") and item["task_id"] in known
                ),
                task,
            )
        if task:
            run = task.get("active_run") or task.get("last_run")
            decision.tasks = []
            decision.controls = []
            if run:
                decision.reply = "I’ll continue in the existing thread."
                if run["state"] in ACTIVE:
                    self.followed_run = run
                    decision.reply = "It’s still running in the existing thread."
                elif run["state"] == "paused":
                    decision.reply = "I’ll resume it in the existing thread."
                    decision.controls = [TaskControl(task_id=task["id"], action="resume")]
                else:
                    decision.controls = [
                        TaskControl(
                            task_id=task["id"],
                            action="retry_review"
                            if self.home.store.interrupted_review(run)
                            else "follow_up",
                            message=""
                            if self.home.store.interrupted_review(run)
                            else "Continue the original task. Inspect completed work first "
                            "and avoid repeating completed actions.",
                        )
                    ]

    def fold_repeats(self) -> None:
        """Repeating an unfinished one-time request continues its existing thread.

        That beats opening a parallel copy that asks the same questions again. Raises
        ``ValueError`` when the decision then changes one task twice.
        """
        decision = self.decision
        fresh: list[Recipe] = []
        for original, recipe in enumerate(decision.tasks):
            existing = next(
                (
                    t
                    for t in self.known.values()
                    if title_key(t["title"]) == title_key(recipe.title)
                    and t.get("project_dir") == recipe.project_dir
                    and (t.get("active_run") or t.get("last_run") or {}).get("state")
                    in ACTIVE | {"blocked", "cancelled", "outcome_unknown", "paused"}
                ),
                None,
            )
            if (
                existing is None
                or recipe.schedule.kind != "once"
                or recipe.schedule.at
                or recipe.after
                or recipe.after_running
                or recipe.after_self_work
            ):
                self.fresh_index[original] = len(fresh)
                fresh.append(recipe)
                continue
            self.followed[original] = existing["id"]
            if any(c.task_id == existing["id"] for c in decision.controls):
                continue
            run = existing.get("active_run") or existing["last_run"]
            if run["state"] in ACTIVE or run["state"] == "outcome_unknown":
                self.followed_run = run
            elif self.home.store.interrupted_review(run):
                decision.controls.append(TaskControl(task_id=existing["id"], action="retry_review"))
            else:
                decision.controls.append(
                    TaskControl(
                        task_id=existing["id"],
                        action="follow_up",
                        message=recipe.prompt,
                        patch={"send_files": True} if recipe.send_files else {},
                    )
                )
        if decision.tasks and not fresh:
            decision.reply = "I’ll continue in the existing thread."
        decision.tasks = fresh
        if len({c.task_id for c in decision.controls}) != len(decision.controls):
            raise ValueError("Rem proposed conflicting changes to the same task")

    def apply_controls(self, db: sqlite3.Connection) -> None:
        """Pause, resume, cancel, follow up on, retry or edit the tasks the decision names.

        Each change applies on its own: one that fails is rolled back and the reply says
        which, while the others still go ahead in the reply's transaction.
        """
        decision, known = self.decision, self.known
        # Rem's reply carries the card of the last followed run, unless it
        # goes with a new task's run instead; other follow-ups get their own.
        carried = (
            None
            if decision.tasks
            else max(
                (
                    index
                    for index, control in enumerate(decision.controls)
                    if control.action in {"follow_up", "retry_review"}
                    or (
                        control.action == "resume"
                        and (known[control.task_id].get("last_run") or {}).get("state") == "paused"
                    )
                ),
                default=None,
            )
        )
        failed: list[str] = []
        for index, control in enumerate(decision.controls):
            db.execute("SAVEPOINT rem_control")
            try:
                followed = self._apply_control(db, index, control, carried)
            except (ValueError, KeyError) as exc:
                db.execute("ROLLBACK TO rem_control")
                title = str((known.get(control.task_id) or {}).get("title") or "a task")
                reason = one_line(str(exc)).rstrip(".") or type(exc).__name__
                failed.append(f"the {CONTROL_WORDS[control.action]} of “{title}”: {reason}")
            else:
                if followed is not None:
                    self.followed_run = followed
            finally:
                db.execute("RELEASE rem_control")
        if failed:
            decision.reply = "\n\n".join([decision.reply, left_out_note(failed)])

    def _apply_control(
        self, db: sqlite3.Connection, index: int, control: TaskControl, carried: int | None
    ) -> dict[str, Any] | None:
        """Apply one control; returns the run it continued, if any."""
        home, pending = self.home, self.pending
        # The reply's task read is from before the model answered; act on the
        # task as it is now, in the transaction that changes it.
        try:
            stored = home.store.get(db, "tasks", control.task_id)
        except KeyError:
            stored = {"deleted_at": "gone"}
        if stored.get("deleted_at"):
            raise ValueError("it was deleted while I was answering")
        task = home.store.task_view(db, stored)
        request_id = "rem-control-" + hashlib.sha256(encode([pending, index]).encode()).hexdigest()
        payload = {"request_id": request_id}
        last = task.get("active_run") or task.get("last_run") or {}
        if control.action == "pause" and task.get("active_run"):
            # A running run pauses; a schedule with nothing running pauses below.
            home.pause_run(task["active_run"]["id"], payload, db=db)
        elif control.action == "resume" and last.get("state") == "paused":
            # Only the user's own message makes the resumed run theirs.
            by = "user" if self.by_user else "rem"
            return cast(dict[str, Any], home.resume_run(last["id"], payload, by=by, db=db)["run"])
        elif control.action == "cancel":
            if task.get("active_run"):
                home.store.mutate_run(task["active_run"]["id"], payload, "cancel", db=db)
        elif control.action in {"follow_up", "retry_review"}:
            run = task.get("active_run") or task.get("last_run")
            if not run:
                raise ValueError("This task has no thread to follow up on")
            if control.action == "retry_review":
                return cast(dict[str, Any], home.retry_review(run["id"], payload, db=db)["run"])
            followed = home.instruct(
                run["thread_id"],
                {**payload, "body": control.message},
                notify=index != carried,
                context=self.delegation_context,
                recipe_patch=control.patch,
                # Work the user asked to continue is theirs, even if self-work started it.
                resumed_by="user" if self.by_user else "rem",
                db=db,
            )["run"]
            return home._deliver_files_to(
                db,
                followed["id"],
                pending if control.patch.get("send_files", task.get("send_files")) else None,
            )
        elif control.action in {"resume", "update"}:
            home.edit_task(
                task["id"],
                {
                    **payload,
                    "expected_revision": task["revision"],
                    **({"enabled": True} if control.action == "resume" else control.patch),
                },
                "edit",
                context=self.delegation_context if control.action == "update" else None,
                db=db,
            )
        else:
            home.edit_task(task["id"], payload, control.action, db=db)
        return None

    def start_tasks(
        self,
        db: sqlite3.Connection,
        config: dict[str, Any],
        note: dict[str, Any],
        changed_tickets: list[dict[str, Any]],
    ) -> None:
        """Add the decision's new tasks; the first one's run or message carries the reply."""
        home, decision, pending = self.home, self.decision, self.pending
        created: list[str] = []
        for index, recipe in enumerate(decision.tasks):
            recipe = recipe.model_copy(
                update={
                    "after": [
                        after_reference(ref, index, created, self.fresh_index, self.followed)
                        for ref in recipe.after
                    ]
                }
            )
            task = home.store.add_task(
                db,
                recipe,
                context=self.delegation_context,
            )
            created.append(task["id"])
            if home.store.starts_now(recipe):
                queued = home.store.add_run(
                    db,
                    task,
                    f"message:{pending}:{index}",
                    config,
                    acknowledgement=decision.reply if index == 0 else None,
                    message_extra=note if index == 0 else None,
                )
                if recipe.send_files:
                    home._deliver_files_to(db, queued["id"], pending)
                # Tickets this reply made go with the work it started.
                link_tickets(db, home.store, queued["id"], changed_tickets)
                task["next_occurrence_at"] = None
                home.store.put(db, "tasks", task)
            else:
                home.store.message(
                    db,
                    decision.reply
                    if index == 0
                    else after_tasks.queued_text(task)
                    if after_tasks.pending(task)
                    else f"Scheduled {task['title']}.",
                    # Found by its ID, like every other answer to a request.
                    identity=f"reply-{pending}" if index == 0 else None,
                    **(note if index == 0 else {}),
                )

    def post_reply(
        self,
        db: sqlite3.Connection,
        config: dict[str, Any],
        note: dict[str, Any],
        file_extra: dict[str, Any],
        remembered: Any,
        changed_tickets: list[dict[str, Any]],
    ) -> None:
        """Post the reply and files messages, then save memories and close the turn.

        ``file_extra`` holds the reply's files and phone action cards, as _reply released
        them; it has ``files`` only when there are some.
        """
        home, decision, pending = self.home, self.decision, self.pending
        files = file_extra.get("files") or []
        if not decision.tasks:
            if self.followed_run:
                self.followed_run = (
                    link_tickets(db, home.store, self.followed_run["id"], changed_tickets)
                    or self.followed_run
                )
            message_extra: dict[str, Any] = (
                {"work": home.store.work(self.followed_run)} if self.followed_run else {}
            )
            message_extra.update(reply_extras(decision, chat_config(config), self.started))
            if self.dropped_actions:
                message_extra["workflow_actions_dropped"] = True
            message_extra.update(note)
            message_extra.update(file_extra)
            home.store.message(
                db,
                decision.reply,
                identity=f"reply-{pending}",
                **message_extra,
            )
        elif file_extra:
            # Task replies are written with their runs; files and phone
            # actions get their own message.
            home.store.message(
                db,
                "Ready on your phone."
                if not files
                else "Here’s what you asked for."
                if len(files) == 1
                else "Here are the files.",
                identity=f"files-{pending}",
                **file_extra,
            )
        if decision.self_work:
            # A self-work report this request produced goes just under the reply.
            home.self_work.post_after_reply(db)
        home.store.set_setting(db, "memories", remembered)
        conversation = home.store.setting(db, "conversation")
        conversation.update(turn_state="idle", processed=pending, turn_label=None, draft_reply=None)
        home.store.set_setting(db, "conversation", conversation)
