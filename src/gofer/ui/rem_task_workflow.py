"""Make it a workflow: turn a repeating Rem task into a Rattish workflow.

After a repeating task (Daily, Weekly or Custom cron) has passed twice, its menu offers
**Make it a workflow**. That starts a normal one-time Rem task in the same project. Its
worker drafts a ``workflow.rattish`` from the task's instructions and the commands and
files its successful runs used, with the workflow-builder skill, and the usual review
checks it. A task that needs open-ended judgment every run gets no draft: the worker
says the switch won't save much and stops.

The drafting task's finished card compares the repeating task's average cost per run
(from the usage ledger) with the workflow's test-run cost. **Switch over** is the
user's alone (agent tokens can't reach ``/api/rem/`` writes): it adds a cron trigger
with the task's schedule and time zone, approves the workflow version the user reviewed
through the triggers' Approved versions flow, and pauses the task's schedule. Resuming
the task's schedule turns that trigger off again.

The worker ends its answer with one line Raticode reads:

- ``Workflow draft: /absolute/path/workflow.rattish``
- ``Workflow draft: not worth it - reason``
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from gofer.ui.rem_home_models import ACTIVE, WEEKDAYS, Recipe, RemConflict, now
from gofer.ui.rem_home_store import NEWEST_RUN

# Schedules a cron trigger can repeat exactly. Interval tasks and one-time tasks can't.
REPEATING = frozenset({"daily", "weekly", "cron"})
# Successful runs a task needs before the menu offers to make it a workflow.
MIN_PASSES = 2
# Successful runs whose commands, files and costs are read.
RECENT_PASSES = 10
MAX_COMMANDS = 40
MAX_FILES = 40
COMMAND_CHARS = 300
MARKER = re.compile(r"^[\s*_>`-]*workflow draft:\s*(?P<value>.+?)[\s*_`]*$", re.I | re.M)
NOT_WORTH_IT = re.compile(r"^not worth it\s*[-–—:]*\s*(?P<reason>.*)$", re.I | re.S)
FILE_KEYS = ("file_path", "path", "notebook_path", "filePath")
SKILL = "skills/raticode-workflow-builder/SKILL.md"


class WorkflowSwitchError(ValueError):
    """The task can't be turned into a workflow or switched over, and why."""


# --------------------------------------------------------------------------- schedule


def trigger_schedule(schedule: dict[str, Any]) -> tuple[str, str]:
    """The cron expression and time zone of a trigger that fires when the task would."""
    kind = schedule.get("kind")
    timezone = str(schedule.get("timezone") or "UTC")
    if kind in {"daily", "weekly"}:
        hour, minute = (int(part) for part in str(schedule.get("local_time") or "09:00").split(":"))
        if kind == "daily":
            return f"{minute} {hour} * * *", timezone
        days = [day for day in WEEKDAYS if day in (schedule.get("weekdays") or [])]
        if not days:
            raise WorkflowSwitchError("This task's weekly schedule has no weekdays.")
        order = ("sunday", *WEEKDAYS[:-1])
        names = ",".join(day[:3] for day in order if day in days)
        return f"{minute} {hour} * * {names}", timezone
    if kind == "cron":
        # Rem stores crontab text with day names, which triggers read the same way.
        fields = str(schedule.get("cron") or "").split()
        if len(fields) != 5:
            raise WorkflowSwitchError("This task's cron schedule needs five fields.")
        return " ".join(fields), timezone
    raise WorkflowSwitchError("Only Daily, Weekly and Custom cron tasks can switch to a workflow.")


def describe_schedule(schedule: dict[str, Any]) -> str:
    kind = schedule.get("kind")
    zone = schedule.get("timezone") or "UTC"
    if kind == "daily":
        return f"every day at {schedule.get('local_time')} ({zone})"
    if kind == "weekly":
        days = ", ".join(day.title() for day in schedule.get("weekdays") or [])
        return f"every {days} at {schedule.get('local_time')} ({zone})"
    if kind == "cron":
        return f"cron {schedule.get('cron')} ({zone})"
    return str(kind or "once")


# --------------------------------------------------------------------------- offer


def _passed_runs(db: sqlite3.Connection, task_id: str, limit: int) -> list[dict[str, Any]]:
    return [
        json.loads(row[0])
        for row in db.execute(
            "SELECT body FROM runs WHERE task=? AND state='succeeded' ORDER BY "
            + NEWEST_RUN
            + " LIMIT ?",
            (task_id, limit),
        )
    ]


def _passes(db: sqlite3.Connection, task_id: str) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM runs WHERE task=? AND state='succeeded'", (task_id,)
    ).fetchone()
    return int(row[0]) if row else 0


def _live_task(db: sqlite3.Connection, identity: str | None) -> dict[str, Any] | None:
    if not identity:
        return None
    row = db.execute("SELECT body FROM tasks WHERE id=?", (identity,)).fetchone()
    task = json.loads(row[0]) if row else None
    return None if not task or task.get("deleted_at") else task


def offer(db: sqlite3.Connection, task: dict[str, Any]) -> dict[str, Any] | None:
    """What the task menu needs to offer Make it a workflow, or None when it can't."""
    schedule = task.get("schedule") or {}
    if (
        task.get("deleted_at")
        or schedule.get("kind") not in REPEATING
        or task.get("type") == "reminder"
        or task.get("execution_mode") == "conversation"
        or not task.get("project_dir")
        or task.get("workflow_draft_for")
    ):
        return None
    # A draft that is still around keeps its own card; delete it to draft again.
    if _live_task(db, (task.get("workflow_draft") or {}).get("task_id")):
        return None
    passes = _passes(db, task["id"])
    return {"passes": passes} if passes >= MIN_PASSES else None


# --------------------------------------------------------------------------- draft


def _trace_input(trace: dict[str, Any]) -> dict[str, Any]:
    raw = trace.get("input")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def _failed(trace: dict[str, Any]) -> bool:
    if "exitCode" in trace:
        return bool(trace["exitCode"] != 0)
    return str(trace.get("status") or "").lower() in {"failed", "error"}


def used_by_runs(runs: list[dict[str, Any]]) -> dict[str, list[str]]:
    """The shell commands that worked and the files touched in the runs' worker turns."""
    commands: dict[str, None] = {}
    files: dict[str, None] = {}
    reports: dict[str, None] = {}
    for run in runs:
        traces = [t for t in run.get("worker_traces") or [] if isinstance(t, dict)]
        failed = {t.get("id") for t in traces if t.get("phase") == "result" and _failed(t)}
        for trace in traces:
            if trace.get("phase") == "result":
                continue
            values = _trace_input(trace)
            command = trace.get("command") or (
                values.get("command") if trace.get("category") == "shell" else None
            )
            if isinstance(command, list):
                command = " ".join(str(part) for part in command)
            if isinstance(command, str) and command.strip() and trace.get("id") not in failed:
                text = " ".join(command.split())
                commands.setdefault(text[:COMMAND_CHARS], None)
            for key in FILE_KEYS:
                value = values.get(key)
                if isinstance(value, str) and value.strip() and "\n" not in value:
                    files.setdefault(value.strip(), None)
        for artifact in run.get("artifacts") or []:
            if isinstance(artifact, dict) and artifact.get("path"):
                reports.setdefault(str(artifact["path"]), None)
    return {
        "commands": list(commands)[:MAX_COMMANDS],
        "files": list(files)[:MAX_FILES],
        "reports": list(reports)[:10],
    }


def draft_prompt(task: dict[str, Any], runs: list[dict[str, Any]], cli: str = "Raticode") -> str:
    """The drafting worker's instructions, built from the task and its successful runs."""
    used = used_by_runs(runs)
    project = task["project_dir"]
    expression, timezone = trigger_schedule(task["schedule"])

    def listed(items: list[str], empty: str) -> str:
        return "\n".join(f"- {item}" for item in items) if items else f"- {empty}"

    summaries = [
        f"- {run.get('scheduled_for') or run.get('created_at')}: "
        f"{(run.get('summary_short') or '').strip() or 'no summary'}"
        for run in runs[:3]
    ]
    return f"""Turn the repeating Rem task "{task["title"]}" into a Rattish workflow that can \
run on its own without a model, or with as few model calls as possible.

The task runs {describe_schedule(task["schedule"])} in {project}. It has passed \
{len(runs)} time(s) recently. Its instructions, word for word:

<task-instructions>
{task.get("base_prompt") or task["prompt"]}
</task-instructions>

What its last successful runs reported:
{chr(10).join(summaries)}

Commands its successful runs used (shell commands that didn't fail):
{listed(used["commands"], "none recorded")}

Files its successful runs read or wrote:
{listed(used["files"], "none recorded")}

Reports they saved:
{listed(used["reports"], "none")}

Do this:

1. First decide whether a fixed workflow can do this work. If every run needs open-ended \
judgment (deciding what to investigate, which change to make or what to write, in a way a \
fixed graph of steps can't capture), draft nothing. End your answer with the line \
"Workflow draft: not worth it - " and one sentence saying why, then stop.
2. Otherwise follow the raticode-workflow-builder skill ({SKILL} in Raticode, or the copy \
in your thread tools). Create the workflow in this project with \
`{cli} rattish create "{task["title"]}" --project {project} --format json` and edit the \
`workflow.rattish` it returns.
3. Make each step that ran the same way every time a `bash-command`, `shell-script` or \
`python-script` node, reusing the commands above. Keep an `agent` or `common-llm-task` node \
only for a step that needs judgment, and say in a `#` comment above it why. Give every \
workflow input a default, so a cron trigger can start it with no inputs. Never write \
credentials into any file; use `secret["NAME"]` and tell the user which names to save.
4. Add exactly one test case to the workflow's test set (`workflow.tests.json` beside it), \
with checks that show the workflow did the task's job.
5. Run `{cli} rattish format`, `{cli} rattish check --format json` and \
`{cli} rattish preflight --format json` on it and fix every error.
6. Run the test case once with `{cli} rattish test PATH --format json`, so Raticode can \
compare what a run costs. If that run would publish, send, deploy, delete or change \
anything outside this project, make the test case safe first (for example an input that \
turns on a dry run) or don't run it and say why.
7. Don't add a trigger, and don't pause, edit or run the repeating task. The user compares \
the costs and switches over from Rem's card, which adds a cron trigger for \
`{expression}` ({timezone}).
8. End your final answer with exactly one line: \
"Workflow draft: " followed by the absolute path of the workflow.rattish file."""


def draft_recipe(task: dict[str, Any], runs: list[dict[str, Any]], cli: str) -> Recipe:
    return Recipe.model_validate(
        {
            "title": f"Make “{task['title'][:120]}” a workflow",
            "type": "develop",
            "prompt": draft_prompt(task, runs, cli),
            "project_dir": task["project_dir"],
            "provider": task.get("provider"),
            "model": task.get("model"),
            "effort": task.get("effort"),
            "acceptance_criteria": [
                {
                    "description": "Either a workflow.rattish drafted from the task's "
                    "instructions and the commands and files its runs used, which passes "
                    "`rattish check` and `rattish preflight` with no errors, or a stated reason "
                    "that the task needs open-ended judgment every run",
                    "authored_by": "rem_recommended",
                },
                {
                    "description": "A drafted workflow's test set has exactly one test case, "
                    "and that case was run once or the answer says why it couldn't be run safely",
                    "authored_by": "rem_recommended",
                },
                {
                    "description": "Agent or model steps appear only where a step needs "
                    "judgment; steps the runs repeated the same way are command or script nodes",
                    "authored_by": "rem_recommended",
                },
                {
                    "description": "No trigger was added and the repeating task was not "
                    "paused, edited or run",
                    "authored_by": "rem_recommended",
                },
                {
                    "description": "The final answer ends with a 'Workflow draft:' line naming "
                    "the workflow.rattish path, or 'not worth it' with the reason",
                    "authored_by": "rem_recommended",
                },
            ],
        }
    )


def start_draft(
    store: Any, task_id: str, payload: dict[str, Any], config: dict[str, Any], cli: str
) -> dict[str, Any]:
    """Start the drafting task for a repeating task. Idempotent by ``request_id``."""

    def operation(db: sqlite3.Connection) -> dict[str, Any]:
        task = store.get(db, "tasks", task_id)
        if task.get("deleted_at"):
            raise KeyError(task_id)
        if (task.get("schedule") or {}).get("kind") not in REPEATING:
            raise WorkflowSwitchError("Only Daily, Weekly and Custom cron tasks can do this.")
        existing = _live_task(db, (task.get("workflow_draft") or {}).get("task_id"))
        if existing:
            raise RemConflict(f"“{existing['title']}” is already drafting this workflow.")
        if not offer(db, task):
            raise WorkflowSwitchError(
                f"A task can become a workflow once it has passed {MIN_PASSES} times."
            )
        runs = _passed_runs(db, task_id, RECENT_PASSES)
        draft = store.add_task(db, draft_recipe(task, runs, cli))
        draft["workflow_draft_for"] = {"task_id": task_id, "title": task["title"]}
        store.put(db, "tasks", draft)
        # Not a change to what the task does, so its revision stays and open editors
        # don't conflict.
        task["workflow_draft"] = {"task_id": draft["id"], "created_at": now()}
        store.put(db, "tasks", task)
        run = store.add_run(
            db,
            draft,
            f"manual:{payload['request_id']}",
            config,
            acknowledgement=f"Started drafting a workflow for “{task['title']}”.",
        )
        store.event(db, "task.workflow_draft", {"task_id": task_id, "draft_id": draft["id"]})
        return {
            "task": store.task_view(db, task),
            "draft": store.task_view(db, draft),
            "run": run,
        }

    if not isinstance(payload.get("request_id"), str) or not payload["request_id"]:
        raise ValueError("request_id is required")
    return dict(store.command(f"task.workflow_draft:{task_id}", payload, operation))


# --------------------------------------------------------------------------- result


def parse_marker(answer: str, project_dir: str | None) -> dict[str, Any]:
    """What the worker's ``Workflow draft:`` line says, checked against the project."""
    found = list(MARKER.finditer(answer or ""))
    if not found:
        return {"status": "missing", "reason": "The worker didn't name a workflow file."}
    value = found[-1].group("value").strip().strip("`\"'")
    skipped = NOT_WORTH_IT.match(value)
    if skipped:
        reason = skipped.group("reason").strip() or "The task needs judgment every run."
        return {"status": "not_worth_it", "reason": reason[:600]}
    path = Path(value).expanduser()
    if not path.is_absolute() or path.suffix != ".rattish":
        return {"status": "missing", "reason": f"{value} isn't a workflow.rattish path."}
    try:
        resolved = path.resolve()
        root = Path(project_dir or "").expanduser().resolve()
    except OSError:
        return {"status": "missing", "reason": f"{value} can't be read."}
    if not project_dir or not resolved.is_relative_to(root):
        return {"status": "missing", "reason": "The workflow isn't inside the task's project."}
    if not resolved.is_file():
        return {"status": "missing", "reason": f"{resolved} doesn't exist anymore."}
    return {"status": "ready", "workflow_path": str(resolved)}


def run_costs(run_ids: list[str], data_dir: Path) -> dict[str, float]:
    """Each run's cost in US dollars, for runs whose every call reported one."""
    from gofer.core.usage_ledger import usage_db

    wanted = [r for r in run_ids if r]
    if not wanted:
        return {}
    totals: dict[str, float] = {}
    unknown: set[str] = set()
    try:
        with usage_db(data_dir) as db:
            rows = db.execute(
                "SELECT run_id, cost_usd, estimated FROM usage_invocations "
                f"WHERE run_id IN ({','.join('?' * len(wanted))})",
                wanted,
            ).fetchall()
    except (OSError, sqlite3.Error, ValueError):
        return {}
    for row in rows:
        if row["cost_usd"] is None or row["estimated"]:
            unknown.add(row["run_id"])
            continue
        totals[row["run_id"]] = totals.get(row["run_id"], 0.0) + float(row["cost_usd"])
    return {run: cost for run, cost in totals.items() if run not in unknown}


def task_cost(db: sqlite3.Connection, task_id: str, data_dir: Path) -> dict[str, Any]:
    """The repeating task's average cost per successful run, from the usage ledger."""
    runs = [r["id"] for r in _passed_runs(db, task_id, RECENT_PASSES)]
    costs = run_costs(runs, data_dir)
    known = [costs[r] for r in runs if r in costs]
    return {
        "runs": len(runs),
        "runs_with_cost": len(known),
        "average_usd": round(sum(known) / len(known), 6) if known else None,
    }


_RESULT_DIRS: dict[tuple[str, int], Path | None] = {}


def _results_dir(source: Path, data_dir: Path) -> Path | None:
    from gofer.rattish.storage import registered_source_root

    root = registered_source_root(source, data_dir)
    if root is not None:
        return root / "logs" / "tests"
    try:
        stamp = source.stat().st_mtime_ns
    except OSError:
        return None
    key = (str(source), stamp)
    if key not in _RESULT_DIRS:
        from gofer.rattish.artifacts import compile_rattish_file
        from gofer.rattish.test_sets import results_directory

        try:
            compiled = compile_rattish_file(source, data_dir=data_dir)
            _RESULT_DIRS[key] = results_directory(
                source, str(compiled.ir["workflow"]["id"]), data_dir
            )
        except Exception:  # noqa: BLE001 - a draft that doesn't compile has no results
            _RESULT_DIRS[key] = None
    return _RESULT_DIRS[key]


def test_cost(source: Path, data_dir: Path) -> dict[str, Any] | None:
    """The newest finished test run's cost for one workflow run (judge checks left out)."""
    from gofer.rattish.test_sets import list_test_results

    directory = _results_dir(source, data_dir)
    if directory is None:
        return None
    for document in list_test_results(directory, limit=10):
        if document.get("status") not in {"passed", "failed"}:
            continue
        for case in document.get("cases") or []:
            if case.get("status") == "skipped":
                continue
            usage = case.get("usage") or {}
            cost = usage.get("costUsd")
            return {
                "result_id": document.get("id"),
                "status": case.get("status"),
                "model_calls": int(usage.get("modelCalls") or 0),
                "cost_usd": float(cost) if isinstance(cost, int | float) else None,
                "finished_at": document.get("finishedAt"),
            }
    return None


def draft_view(db: sqlite3.Connection, task: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    """The drafting task's finished card: its verdict, the costs and the switch."""
    origin = task["workflow_draft_for"]
    source = _live_task(db, origin.get("task_id"))
    view: dict[str, Any] = {
        "source_task_id": origin.get("task_id"),
        "source_title": (source or {}).get("title") or origin.get("title"),
        "source_deleted": source is None,
        "status": "drafting",
    }
    row = db.execute(
        "SELECT json_extract(body,'$.worker_result'), json_extract(body,'$.state') FROM runs "
        "WHERE task=? ORDER BY " + NEWEST_RUN + " LIMIT 1",
        (task["id"],),
    ).fetchone()
    if not row or row[1] != "succeeded":
        if row and row[1] not in ACTIVE and row[1] != "paused":
            view["status"] = "unfinished"
        return view
    view.update(parse_marker(str(row[0] or ""), task.get("project_dir")))
    if source is not None:
        view["schedule"] = describe_schedule(source.get("schedule") or {})
        view["task_cost"] = task_cost(db, source["id"], data_dir)
        switch = source.get("workflow_switch") or {}
        if switch:
            view["switch"] = switch
        view["task_schedule_paused"] = source.get("state") == "paused"
    if view["status"] == "ready":
        view["test_cost"] = test_cost(Path(view["workflow_path"]), data_dir)
    return view


# --------------------------------------------------------------------------- switch


def _draft_and_source(
    db: sqlite3.Connection, store: Any, task_id: str, data_dir: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    task = store.get(db, "tasks", task_id)
    if not task.get("workflow_draft_for"):
        # Asked on the repeating task itself: use its draft.
        draft_id = (task.get("workflow_draft") or {}).get("task_id")
        drafted = _live_task(db, draft_id)
        if not drafted:
            raise WorkflowSwitchError("This task has no workflow draft yet.")
        task = drafted
    view = draft_view(db, task, data_dir)
    if view["status"] != "ready":
        raise WorkflowSwitchError(
            view.get("reason") or "The workflow draft isn't finished, so there's nothing to switch."
        )
    source = _live_task(db, view["source_task_id"])
    if source is None:
        raise WorkflowSwitchError("The repeating task was deleted, so there's no schedule to copy.")
    return task, source, view


def preview(store: Any, task_id: str, data_dir: Path) -> dict[str, Any]:
    """What Switch over would do, and the workflow files to review before approving."""
    from gofer.rattish.trigger_approval import describe_changes, take_snapshot
    from gofer.rattish.triggers import preview_cron

    with store.read() as db:
        _, source, view = _draft_and_source(db, store, task_id, data_dir)
    expression, timezone = trigger_schedule(source["schedule"])
    workflow = Path(view["workflow_path"])
    current = take_snapshot(workflow, data_dir)
    if not current.complete:
        raise WorkflowSwitchError(
            "The workflow doesn't compile now. Open it in the Studio and fix it first."
        )
    changes = describe_changes(None, current, project_root=Path(source["project_dir"]))
    return {
        "workflow_path": str(workflow),
        "workflowName": current.workflow_name,
        "cron": expression,
        "timezone": timezone,
        "next_runs": preview_cron(expression, timezone),
        "schedule": describe_schedule(source["schedule"]),
        "changes": {
            **changes,
            "state": "unapproved",
            "approvedFingerprint": None,
        },
    }


def switch_over(
    store: Any,
    task_id: str,
    payload: dict[str, Any],
    data_dir: Path,
    pause: Any,
) -> dict[str, Any]:
    """Add the cron trigger, approve the reviewed version and pause the task's schedule.

    ``pause(task_id)`` runs Pause schedule on the repeating task. Approval goes through
    ``approve_trigger`` with the fingerprint the user reviewed, so a workflow changed since
    is refused and nothing switches.
    """
    from gofer.rattish.triggers import TriggerNotFound, TriggerStore, approve_trigger

    fingerprint = payload.get("fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise WorkflowSwitchError("Review the workflow before switching over.")
    accept_unseen = payload.get("accept_unseen", False)
    if not isinstance(accept_unseen, bool):
        raise ValueError("accept_unseen must be true or false")
    with store.read() as db:
        _, source, view = _draft_and_source(db, store, task_id, data_dir)
    expression, timezone = trigger_schedule(source["schedule"])
    triggers = TriggerStore(data_dir)
    previous = (source.get("workflow_switch") or {}).get("trigger_id")
    created = False
    record = None
    if previous:
        try:
            record = triggers.get(previous)
            if record.source_path != view["workflow_path"]:
                record = None
            else:
                record = triggers.update(
                    previous,
                    config={"expression": expression, "timezone": timezone},
                    enabled=True,
                    actor="user",
                )
        except TriggerNotFound:
            record = None
    if record is None:
        # Saved as Rem's: it waits for the approval below, which is the user's.
        record, _ = triggers.create(
            kind="cron",
            source_path=view["workflow_path"],
            config={"expression": expression, "timezone": timezone},
            label=f"Switched over from Rem task “{source['title'][:80]}”",
            actor="rem",
        )
        created = True
    try:
        approve_trigger(
            triggers,
            record.id,
            actor="user",
            by="desktop",
            expected_fingerprint=fingerprint,
            accept_unseen=accept_unseen,
        )
    except Exception:
        if created:
            triggers.delete(record.id)
        elif previous:
            triggers.update(record.id, enabled=False, actor="user")
        raise
    switch = {
        "trigger_id": record.id,
        "workflow_path": view["workflow_path"],
        "cron": expression,
        "timezone": timezone,
        "switched_at": now(),
        "active": True,
    }
    with store.connect() as db:
        task = store.get(db, "tasks", source["id"])
        task["workflow_switch"] = switch
        store.put(db, "tasks", task)
        store.event(db, "task.workflow_switch", {"task_id": source["id"], "trigger": record.id})
    if source.get("state") != "paused":
        pause(source["id"])
    with store.read() as db:
        current = store.get(db, "tasks", source["id"])
        return {"task": store.task_view(db, current), "trigger_id": record.id, "switch": switch}


def mark_undone(store: Any, db: sqlite3.Connection, task_id: str) -> bool:
    """Record, in the caller's transaction, that resuming the task undid Switch over.

    The trigger lives in another database, so ``settle_switches`` turns it off once this
    transaction has committed. A transaction that rolls back leaves both as they were,
    so the task can't end up paused with its workflow's trigger off.
    """
    task = store.get(db, "tasks", task_id)
    switch = task.get("workflow_switch") or {}
    if not switch.get("active") or task.get("state") == "paused":
        return False
    task["workflow_switch"] = {**switch, "active": False, "undone_at": now(), "trigger_off": False}
    store.put(db, "tasks", task)
    store.event(db, "task.workflow_switch_undone", {"task_id": task_id})
    return True


def settle_switches(store: Any, data_dir: Path) -> int:
    """Turn off the trigger of each switch a resumed task undid. Safe to repeat."""
    from gofer.rattish.triggers import TriggerNotFound, TriggerStore

    with store.read() as db:
        pending = [
            row[0]
            for row in db.execute(
                "SELECT id FROM tasks WHERE json_extract(body, '$.workflow_switch.trigger_off') = 0"
            )
        ]
    settled = 0
    for task_id in pending:
        with store.connect() as db:
            task = store.get(db, "tasks", task_id)
            switch = task.get("workflow_switch") or {}
            # Switched over again since: that switch's trigger stays on.
            if switch.get("active") or switch.get("trigger_off") is not False:
                continue
            try:
                TriggerStore(data_dir).update(switch["trigger_id"], enabled=False, actor="user")
            except TriggerNotFound:
                pass
            task["workflow_switch"] = {**switch, "trigger_off": True}
            store.put(db, "tasks", task)
            settled += 1
    return settled


def undo_switch(
    store: Any, task_id: str, data_dir: Path, db: sqlite3.Connection | None = None
) -> bool:
    """Resume schedule undoes Switch over: the workflow's trigger is turned off.

    With ``db``, the caller's transaction records it and the caller runs
    ``settle_switches`` after committing (Rem's tick does too).
    """
    if db is not None:
        return mark_undone(store, db, task_id)
    with store.connect() as own:
        undone = mark_undone(store, own, task_id)
    if undone:
        settle_switches(store, data_dir)
    return undone


def views(db: sqlite3.Connection, task: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    """Fields task views add for this feature."""
    extra: dict[str, Any] = {}
    if task.get("workflow_draft_for"):
        extra["workflow_draft_result"] = draft_view(db, task, data_dir)
    else:
        offered = offer(db, task)
        if offered:
            extra["workflow_offer"] = offered
    return extra
