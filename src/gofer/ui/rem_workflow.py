"""Rem prompts and Rem task runs that workflow nodes start and wait for.

Workflows run in the desktop backend, from the CLI and on schedules, so the nodes
talk to Rem through its durable store, as the desktop's own requests do. The
backend that holds Rem's lease answers a queued message and starts a queued run,
and it checks the run against Rem's current project and folder grants before any
worker starts. A node never starts work Rem itself would refuse.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import anyio

from gofer.ui.rem_home import MAX_FOLLOW_UPS, follow_up_prompt, queue_user_message
from gofer.ui.rem_home_models import ACTIVE, RemConflict, now
from gofer.ui.rem_home_store import NEWEST_RUN, RemHomeStore

# How often a waiting node checks Rem's store.
POLL_SECONDS = 0.5
# How long a node waits for Rem to come back before it gives up. A backend
# restarting takes a few seconds; Rem's lease lasts 15 seconds after its last check.
ABSENT_GRACE_SECONDS = 60.0
# The most of a worker's final answer a task node passes on.
RESULT_LIMIT = 20000
STORE_FILE = "rem-home.sqlite3"


class RemWorkflowError(Exception):
    """A Rem node could not finish; ``kind`` and ``code`` describe why."""

    def __init__(
        self,
        kind: str,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
        output: Any = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.code = code
        self.message = message
        self.details = dict(details or {})
        self.output = output


@dataclass(frozen=True, slots=True)
class WorkflowOrigin:
    """The workflow run and node behind a Rem request, shown in Rem's chat and runs."""

    workflow_id: str
    run_id: str
    node_id: str
    name: str = ""

    def document(self) -> dict[str, str]:
        return {
            "id": self.workflow_id,
            "name": self.name or self.workflow_id,
            "run_id": self.run_id,
            "node_id": self.node_id,
        }


@dataclass(slots=True)
class _Presence:
    """When Rem was last seen holding its lease, for the absence grace period."""

    seen: float = field(default_factory=time.monotonic)

    def check(self, store: RemHomeStore) -> bool:
        if rem_running(store):
            self.seen = time.monotonic()
            return True
        return time.monotonic() - self.seen < ABSENT_GRACE_SECONDS


def unavailable(message: str | None = None) -> RemWorkflowError:
    return RemWorkflowError(
        "rem",
        "RATTISH_REM_UNAVAILABLE",
        message
        or "Rem isn't running. Open Raticode so Rem can answer, then run the workflow again.",
    )


def existing_store(data_dir: Path) -> RemHomeStore | None:
    """Rem's store in ``data_dir``, or None when Rem has never been used there."""
    if not (data_dir / STORE_FILE).is_file():
        return None
    return RemHomeStore(data_dir)


def rem_running(store: RemHomeStore) -> bool:
    """Whether a Raticode backend currently holds Rem's lease and answers requests."""
    with store.read() as db:
        row = db.execute("SELECT expires FROM lease WHERE id=1").fetchone()
    return bool(row and row["expires"] and float(row["expires"]) > time.time())


def missing_task(task_id: str) -> RemWorkflowError:
    return RemWorkflowError(
        "rem",
        "RATTISH_REM_TASK_UNAVAILABLE",
        f"Rem task {task_id!r} doesn't exist or was deleted. Choose another task.",
        {"task_id": task_id},
    )


def task_record(store: RemHomeStore, task_id: str) -> dict[str, Any]:
    """The live task, or a RATTISH_REM_TASK_UNAVAILABLE error."""
    with store.read() as db:
        row = db.execute("SELECT body FROM tasks WHERE id=?", (task_id,)).fetchone()
    task = json.loads(row[0]) if row else None
    if not task or task.get("deleted_at"):
        raise missing_task(task_id)
    return cast(dict[str, Any], task)


def live_tasks(data_dir: Path) -> list[dict[str, Any]]:
    """Rem's tasks a workflow can start, newest first, as ``{id, title, type, state}``."""
    store = existing_store(data_dir)
    if store is None:
        return []
    with store.read() as db:
        tasks = [
            task
            for row in db.execute("SELECT body FROM tasks")
            if not (task := json.loads(row[0])).get("deleted_at")
        ]
    tasks.sort(key=lambda task: str(task.get("created_at") or ""), reverse=True)
    return [
        {
            "id": str(task["id"]),
            "title": str(task.get("title") or ""),
            "type": str(task.get("type") or ""),
            "state": str(task.get("state") or ""),
            "project_dir": task.get("project_dir"),
        }
        for task in tasks
    ]


# Prompts ------------------------------------------------------------------------------


def submit_prompt(
    store: RemHomeStore,
    origin: WorkflowOrigin,
    body: str,
    *,
    allow_actions: bool,
    request_id: str | None = None,
) -> str:
    """Queue ``body`` in Rem's chat as the user's workflow asking; returns its message ID."""
    text = body.strip()
    if not text or len(text) > 100000:
        raise RemWorkflowError(
            "configuration",
            "RATTISH_REM_PROMPT_INVALID",
            "A Rem prompt needs between 1 and 100,000 characters.",
        )
    identity = request_id or f"workflow-{uuid4().hex}"
    workflow = {**origin.document(), "allow_actions": allow_actions}
    entry = {
        "id": identity,
        "body": text,
        "created_at": now(),
        "source": "workflow",
        "workflow": workflow,
    }
    try:
        store.command(
            "message",
            {"request_id": identity, "body": text, "source": "workflow", "workflow": workflow},
            lambda db: queue_user_message(store, db, entry, owner=f"workflow:{origin.run_id}"),
        )
    except RemConflict as exc:
        raise RemWorkflowError("rem", "RATTISH_REM_BUSY", str(exc)) from exc
    return identity


def withdraw_prompt(store: RemHomeStore, request_id: str) -> bool:
    """Take a prompt Rem hasn't started answering out of its queue."""
    with store.connect() as db:
        conversation = store.setting(db, "conversation")
        waiting = list(conversation.get("waiting") or [])
        kept = [entry for entry in waiting if entry.get("id") != request_id]
        if len(kept) == len(waiting):
            return False
        conversation["waiting"] = kept
        store.set_setting(db, "conversation", conversation)
        return True


def prompt_answer(store: RemHomeStore, request_id: str) -> dict[str, Any] | None:
    """Rem's answer to a queued prompt once it is written, else None.

    One decision writes its reply, task cards and runs in one transaction, so
    whichever of them exists means the whole answer exists.
    """
    with store.read() as db:
        row = db.execute(
            "SELECT body FROM messages WHERE id=?", (f"reply-{request_id}",)
        ).fetchone()
        reply = json.loads(row[0]) if row else None
        runs = [
            json.loads(item[0])
            for item in db.execute(
                "SELECT body FROM runs WHERE occurrence LIKE ? ORDER BY occurrence",
                (f"message:{request_id}:%",),
            )
        ]
        cards: dict[str, dict[str, Any]] = {}
        for run in runs:
            card = db.execute(
                "SELECT body FROM messages WHERE id=?", (f"queued-{run['id']}",)
            ).fetchone()
            if card:
                cards[f"queued-{run['id']}"] = json.loads(card[0])
        if reply is None and not runs:
            asked = db.execute("SELECT 1 FROM messages WHERE id=?", (request_id,)).fetchone()
            waiting = store.setting(db, "conversation").get("waiting") or []
            if not asked and not any(entry.get("id") == request_id for entry in waiting):
                raise RemWorkflowError(
                    "rem",
                    "RATTISH_REM_PROMPT_LOST",
                    "Rem's chat history was cleared before it answered this workflow.",
                    {"request_id": request_id},
                )
            return None
    if reply is not None and reply.get("diagnostic"):
        raise RemWorkflowError(
            "rem",
            "RATTISH_REM_PROMPT_FAILED",
            str(reply.get("body") or "Rem couldn't answer this workflow."),
            {"request_id": request_id, "message_id": reply["id"]},
        )
    first = reply or cards.get(f"queued-{runs[0]['id']}") or {}
    work = [store.work(run) for run in runs]
    followed = (reply or {}).get("work")
    if followed and not any(item["run_id"] == followed["run_id"] for item in work):
        work.append(followed)
    body = str(first.get("body") or "")
    return {
        "reply": body,
        "details": str(first.get("details") or ""),
        "request_id": request_id,
        "message_id": str(first.get("id") or ""),
        "actions_dropped": bool(first.get("workflow_actions_dropped")),
        "work": work,
    }


async def wait_for_answer(
    store: RemHomeStore, request_id: str, *, poll: float | None = None
) -> dict[str, Any]:
    presence = _Presence()
    while True:
        answer = prompt_answer(store, request_id)
        if answer is not None:
            return answer
        if not presence.check(store):
            raise unavailable(
                "Rem stopped before it answered this workflow. Open Raticode and run it again."
            )
        await anyio.sleep(POLL_SECONDS if poll is None else poll)


# Task runs ----------------------------------------------------------------------------


def start_task(
    store: RemHomeStore,
    task_id: str,
    origin: WorkflowOrigin,
    *,
    instructions: str = "",
    request_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Queue a run of an existing Rem task, as its Run button does.

    Returns the run and whether it was already running for this workflow node
    (a resumed workflow waits for that run instead of starting a second one).
    Extra ``instructions`` apply to this run only; the task itself is unchanged.
    """
    identity = request_id or f"workflow-{uuid4().hex}"
    extra = instructions.strip()

    def operation(db: Any) -> dict[str, Any]:
        row = db.execute("SELECT body FROM tasks WHERE id=?", (task_id,)).fetchone()
        task = json.loads(row[0]) if row else None
        if not task or task.get("deleted_at"):
            raise missing_task(task_id)
        active = db.execute(
            "SELECT body FROM runs WHERE task=? AND state IN ("
            + ",".join("?" * len(ACTIVE))
            + ") ORDER BY "
            + NEWEST_RUN
            + " LIMIT 1",
            (task_id, *sorted(ACTIVE)),
        ).fetchone()
        if active:
            run = json.loads(active[0])
            started_by = run.get("workflow_origin") or {}
            if (
                run["state"] != "cancelling"
                and started_by.get("id") == origin.workflow_id
                and started_by.get("node_id") == origin.node_id
            ):
                return {"run": run, "attached": True}
            raise RemConflict(
                f"{task['title']} is already running. Wait for it to finish or stop it in Rem."
            )
        config = store.setting(db, "config")
        previous = db.execute(
            "SELECT body FROM runs WHERE task=? ORDER BY " + NEWEST_RUN + " LIMIT 1", (task_id,)
        ).fetchone()
        old_run = json.loads(previous[0]) if previous else {}
        if (
            not extra
            and store.interrupted_review(old_run)
            and old_run["recipe"].get("revision") == task["revision"]
        ):
            # The Run button resumes a review that was cut off instead of redoing the work.
            run = store.queue_review(db, old_run, config)
        else:
            source = dict(task)
            if extra:
                base = task.get("base_prompt") or task["prompt"]
                follow_ups = [*(task.get("follow_ups") or []), extra][-MAX_FOLLOW_UPS:]
                source.update(
                    prompt=follow_up_prompt(base, follow_ups),
                    base_prompt=base,
                    follow_ups=follow_ups,
                )
            run = store.add_run(
                db,
                source,
                f"manual:{identity}",
                config,
                acknowledgement=f"Started {task['title']} for the workflow "
                f"{origin.name or origin.workflow_id}.",
            )
        run["workflow_origin"] = origin.document()
        store.put(db, "runs", run)
        return {"run": run, "attached": False}

    try:
        result = store.command(
            f"task.workflow_run:{task_id}",
            {"request_id": identity, "instructions": extra, "workflow": origin.document()},
            operation,
        )
    except RemConflict as exc:
        raise RemWorkflowError("rem", "RATTISH_REM_BUSY", str(exc), {"task_id": task_id}) from exc
    return result["run"], bool(result["attached"])


def current_run(store: RemHomeStore, run_id: str) -> dict[str, Any]:
    with store.read() as db:
        row = db.execute("SELECT body FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise RemWorkflowError(
            "rem",
            "RATTISH_REM_RUN_MISSING",
            "The Rem run this workflow started is no longer saved.",
            {"run_id": run_id},
        )
    return cast(dict[str, Any], json.loads(row[0]))


def cancel_run(store: RemHomeStore, run_id: str) -> None:
    """Stop a run the workflow started; Rem stops its worker as the Stop button does."""
    store.mutate_run(run_id, {"request_id": f"workflow-stop-{run_id}"}, "cancel")


def run_output(run: Mapping[str, Any], *, attached: bool = False) -> dict[str, Any]:
    """What a task node passes to the next steps."""
    return {
        "task_id": str(run["task_id"]),
        "run_id": str(run["id"]),
        "title": str(run["recipe"]["title"]),
        "state": str(run["state"]),
        "succeeded": run["state"] == "succeeded",
        "summary": str(run.get("summary_short") or ""),
        "result": str(run.get("worker_result") or "")[:RESULT_LIMIT],
        "artifacts": [
            str(item["path"])
            for item in run.get("artifacts") or []
            if item.get("epoch") == run.get("epoch")
            and not item.get("superseded")
            and item.get("path")
        ],
        "thread_id": str(run["thread_id"]),
        "attached": attached,
    }


_OUTCOMES = {
    "failed": ("rem", "RATTISH_REM_TASK_FAILED", "failed"),
    "blocked": ("rem", "RATTISH_REM_TASK_BLOCKED", "couldn't finish"),
    "cancelled": ("cancelled", "RATTISH_REM_TASK_CANCELLED", "was stopped"),
    "outcome_unknown": ("rem", "RATTISH_REM_TASK_OUTCOME_UNKNOWN", "ended with an unknown result"),
}


def run_failure(run: Mapping[str, Any], *, attached: bool = False) -> RemWorkflowError:
    kind, code, verb = _OUTCOMES.get(
        str(run["state"]), ("rem", "RATTISH_REM_TASK_FAILED", "didn't succeed")
    )
    output = run_output(run, attached=attached)
    reason = output["summary"] or str(run.get("stop_reason") or "")
    question = str(run.get("user_question") or "")
    message = f"Rem task {output['title']!r} {verb}."
    if reason:
        message += f" {reason}"
    if run.get("needs_user"):
        message += " It needs your answer in Rem before it can continue."
    return RemWorkflowError(
        kind,
        code,
        message,
        {
            "task_id": output["task_id"],
            "run_id": output["run_id"],
            "state": output["state"],
            "thread_id": output["thread_id"],
            "needs_user": bool(run.get("needs_user")),
            **({"question": question} if question else {}),
        },
        output,
    )


async def wait_for_run(
    store: RemHomeStore, run_id: str, *, poll: float | None = None
) -> dict[str, Any]:
    """The run once Rem finishes it; raises when Rem stays away too long."""
    presence = _Presence()
    while True:
        run = current_run(store, run_id)
        if run["state"] not in ACTIVE:
            return run
        if not presence.check(store):
            raise unavailable(
                "Rem stopped while this workflow waited for its task. "
                "Open Raticode and run it again."
            )
        await anyio.sleep(POLL_SECONDS if poll is None else poll)
