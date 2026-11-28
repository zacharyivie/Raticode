"""Workflows on a paired phone: the list, one workflow's graph and runs, and controls.

The phone sees Rattish workflows in desktop projects its catalog names, the same
scope as swarms. Run, stop and resume call the functions the desktop window
calls, so every desktop check still applies: the run must use the saved source
revision the phone was shown, active-run limits and approval gates hold, and a
resume follows the desktop's resume rules (``resume_rattish_run``) and starts a
new run. The phone edits a workflow only through Use this model after Try another
model (``workflow_tests``), which goes through the desktop's editor.

A run can take typed inputs. ``workflows.read`` lists the workflow's inputs as form
fields built from the same input schema the desktop's Run dialog reads, and
``workflows.run`` sends the values back in ``inputs``. The desktop checks them
against that schema before anything starts, and the phone guard has already
screened them like any other request text. A phone that can show the form says so
with ``input_form`` on its read; older phones are still told to start such a
workflow on the desktop. Inputs whose schema says ``format: password`` are masked
on the phone and their defaults stay here, as do workflow secrets.

Each entry on ``workflows.list`` names its plain text inputs in ``text_inputs``, so the
phone's Run a workflow share target can offer only workflows that can take shared
text. The shared text still goes through ``workflows.run`` and the phone guard like
anything typed into the Run form. ``file_inputs`` names the file inputs, with the kinds
and size each takes, for a shared file. The phone uploads that file on its Rem file
channel the way Share to Rem does, then names it in ``workflows.run``'s ``file_ids``;
the request passes the phone guard like any other, and the desktop copies the file into
the run (``_uploaded_files``).

Resume and ``workflows.rerun`` (Run again) use the inputs and trigger events the run
saved on the computer. The phone sends no values with them, so a run whose password
inputs weren't saved can't be resumed or run again from here: the control's reason
names those inputs and points to the computer.

Each workflow's triggers (``triggers.py``) are listed on its read with their kind,
next run and last result, and ``workflows.trigger`` turns one on or off. The list
read says whether all triggers are paused, and ``workflows.triggers.pause`` pauses or
resumes them all; pausing never changes a trigger's own setting. Turning a trigger on,
or resuming them all, starts work with nobody watching, so the phone asks first and
sends ``confirmed``; the desktop refuses it without. Like every phone request, both
pass the phone guard (``phone_guard.py``) first, so a locked phone can't. The phone
can't add, edit or delete triggers, and never sees a webhook's address or token.
Each trigger's daily limit and the computer-wide one show as ``daily_limit`` text
with today's use, and ``limit_reached`` says starts are being skipped until midnight.
Each trigger's Time limit shows read-only as ``time_limit`` text ("Time limit: 6 h" or
"No time limit"); it is set on the computer.
A triggered run that failed at its provider's usage limit and waits for the reset keeps
its failed status, with "Waiting for usage limit" as its summary, and its trigger's last
result is ``waiting`` until the scheduler resumes it.
When a limit is reached the phone also gets one Workflow message that day
(``trigger_service``).

A trigger runs only the workflow version the user approved (``trigger_approval``). Each
listed trigger says whether its workflow changed since then. ``workflows.trigger.changes``
reads the change as a diff, with paths inside the project shown relative to it, and
``workflows.trigger.approve`` approves exactly the version the phone was shown: the phone
sends its fingerprint, and a workflow that changed again in between is refused as a
conflict. The phone has the user's trust, so this is the user's approval; like every phone
request it passes the phone guard first. Turning a trigger on from the phone approves
nothing, as on the desktop: its answer says when a change is waiting, and the phone opens
Review changes. Each changed file says whether the phone was shown all of its change
(``unseen``: its diff was cut at ``DIFF_LIMIT`` or isn't available), with the path to
open it through ``files.text`` under ``project_id``. When any was, approving also needs
``unseen_confirmed``, sent after the phone asked a second time.

``workflows.failure`` reads what Ask Rem about this failure attaches for one run
(``gofer.ui.run_failure``): the end of the failing node's output, its error, preflight
problems and the run's input values, with password inputs masked, all marked as
untrusted run output. The phone puts it in Rem's message box as a file and sends
nothing until the user does. The phone has the desktop's trust, and the text goes
back to this computer's Rem anyway.

``workflows.tests``, ``workflows.tests.estimate``, ``workflows.tests.try`` and
``workflows.tests.use_model`` are Try another model on the workflow's test set
(``workflow_tests``).

Other results carry no source, run input values, node outputs or provider
configuration. Free text such as errors has the project and data folders replaced
by names.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections.abc import Callable, Coroutine, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gofer.devices.desktop_control import Refused

logger = logging.getLogger(__name__)

READS = frozenset(
    {
        "workflows.failure",
        "workflows.list",
        "workflows.read",
        "workflows.runs",
        "workflows.trigger.changes",
        "workflows.tests",
        "workflows.tests.estimate",
    }
)
ACTIONS = frozenset(
    {
        "workflows.tests.try",
        "workflows.tests.use_model",
        "workflows.run",
        "workflows.stop",
        "workflows.resume",
        "workflows.rerun",
        "workflows.trigger",
        "workflows.trigger.approve",
        "workflows.triggers.pause",
    }
)
# The phone's request is answered on the device loop under the device lock. A
# run start normally acknowledges well within this; a slower one keeps going
# and appears in the run list.
START_WAIT = 5.0
LIST_LIMIT = 100
RUN_LIMIT = 20
# Runs on the phone's all-runs list, newest first; ``earlier`` counts the rest.
ALL_RUNS_LIMIT = 60
# The time ranges the phone's all-runs list offers.
RUN_SPANS = frozenset({"24h", "7d", "30d"})
NODE_LIMIT = 200
EDGE_LIMIT = 400
# Run inputs. The phone guard screens at most 64 KiB of a request's text, so the
# values a run accepts stay well under that.
INPUT_LIMIT = 64
CHOICE_LIMIT = 100
DEFAULT_LIMIT = 4000
INPUTS_LIMIT = 32 * 1024
INPUT_DEPTH = 16
NOT_OPEN = "This workflow is not in an open desktop project."
NEEDS_INPUTS = "This workflow needs inputs. Start it on your computer."
CHANGED = "This workflow changed on your computer. Review it again before running."
REFRESH = "Refresh this workflow, then run it again."
INVALID = "This workflow has errors. Open it on your computer to fix them."
NOT_RUNNING = "No run is going right now."
NO_RUN = "There is no run to resume."
NO_RESUME = "Your computer can't resume this run."
NO_RERUN = "There is no run to run again."
NO_FAILURE = "This run or step is no longer on your computer."
STILL_STARTING = "Your computer is still starting this run. It will appear in the run list."
BAD_INPUTS = "Send the run's inputs as named values."
TOO_LARGE = "These inputs are too large to send from the phone. Start this run on your computer."
UPLOAD_GONE = "The shared file is no longer on your computer. Share it again."
# Triggers shown on one workflow's page.
TRIGGER_LIMIT = 50
CONFIRM_TRIGGER = "Confirm on your phone before turning this trigger on."
CONFIRM_RESUME = "Confirm on your phone before resuming all triggers."
NO_TRIGGER = "This trigger no longer exists on your computer."
CHANGED_AGAIN = "The workflow changed again since you opened its changes. Review them again."
REVIEW_FIRST = "Open the changes before approving them."
UNSEEN_FIRST = (
    "Some changes weren't shown on your phone: {files}. Update Raticode Mobile to confirm "
    "them, or review and approve them on your computer."
)
# The diff the phone shows for a trigger's changes.
DIFF_LIMIT = 60_000
APPROVAL_LINES = {
    "changed": "Workflow changed since you approved it.",
    "unapproved": "Waiting for you to approve this workflow version.",
}
CHANGED_DURING_RUN = "changed during a triggered run, so the step that uses it wasn't run."
TRIGGER_KINDS = {"cron": "Schedule", "file": "File change", "webhook": "Webhook"}
RUN_STATUS = {
    "running": "running",
    "success": "succeeded",
    "error": "failed",
    "stopped": "stopped",
    "disconnected": "interrupted",
}
# Run rows that say why the run stopped, cut like the desktop's run lists.
ENDED_WITH_ERROR = frozenset({"failed", "interrupted", "stopped"})
RUN_ERROR_LIMIT = 300
NODE_STATUS = {
    "success": "succeeded",
    "allowed_failure": "failed",
    "error": "failed",
    "stopped": "stopped",
    "started": "running",
    "retried": "running",
    "disconnected": "interrupted",
}
EDGE_LABELS = {"unconditional": "", "conditional": "when", "otherwise": "otherwise"}


def _text(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def _same(path: str | Path) -> str:
    return os.path.realpath(os.fspath(path))


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def _duration(seconds: float) -> str:
    whole = round(seconds)
    if whole < 60:
        return f"{whole} s"
    if whole < 3600:
        return f"{whole // 60} min {whole % 60} s"
    return f"{whole // 3600} h {whole % 3600 // 60} min"


def _compile(registered: Any, base: Path) -> dict[str, Any] | None:
    from gofer.rattish.artifacts import RattishArtifactError, compile_rattish_file
    from gofer.rattish.diagnostics import RattishError

    try:
        artifact = compile_rattish_file(
            registered.entrypoint,
            data_dir=base,
            workflow_id=registered.workflow_id,
            project_root=registered.project_root,
        )
    except (RattishArtifactError, RattishError, OSError):
        return None
    return dict(artifact.ir)


def _compile_error(registered: Any, base: Path) -> str:
    """The desktop workflow list's one-line reason a workflow doesn't compile."""
    from gofer.rattish.artifacts import RattishArtifactError, compile_rattish_file
    from gofer.rattish.diagnostics import RattishError
    from gofer.ui.api import _rattish_validation_summary

    try:
        compile_rattish_file(
            registered.entrypoint,
            data_dir=base,
            workflow_id=registered.workflow_id,
            project_root=registered.project_root,
        )
    except (RattishArtifactError, RattishError, OSError) as exc:
        return _rattish_validation_summary(exc)
    return ""


def _missing_inputs(ir: dict[str, Any]) -> list[str]:
    """Inputs a run needs that have no default. The phone can't supply them."""
    return [
        str(item["name"])
        for item in ir["workflow"]["inputs"]
        if item["required"] and not item["default"]["present"]
    ]


def _input_kind(schema: dict[str, Any]) -> str:
    """The phone field for an input: the same split the desktop's Run dialog makes."""
    from gofer.rattish.file_inputs import is_file_schema

    if is_file_schema(schema):
        return "file"
    choices = schema.get("enum")
    if isinstance(choices, list) and 0 < len(choices) <= CHOICE_LIMIT:
        return "choice"
    kind = schema.get("type", "string" if not isinstance(choices, list) else None)
    if isinstance(kind, list):
        kind = next((value for value in kind if value != "null"), None)
    return {
        "string": "text",
        "integer": "integer",
        "number": "number",
        "boolean": "boolean",
    }.get(str(kind), "json")


def _secret_input(schema: dict[str, Any]) -> bool:
    """A password input, or one with a password anywhere inside, as run saving decides."""
    from gofer.rattish.run_values import _schema_is_password

    return _schema_is_password(schema)


def _input_fields(ir: Any) -> list[dict[str, Any]]:
    """The workflow's inputs as the phone's Run form shows them."""
    if not isinstance(ir, dict):
        return []
    fields = []
    for item in ir["workflow"]["inputs"][:INPUT_LIMIT]:
        schema = item.get("schema") if isinstance(item.get("schema"), dict) else {}
        name = _text(item["name"], 200)
        kind = _input_kind(schema)
        secret = _secret_input(schema)
        field: dict[str, Any] = {
            "name": name,
            "label": _text(schema.get("title") or name, 200),
            "description": _text(schema.get("description"), 300),
            "kind": kind,
            "required": bool(item["required"]),
            "secret": secret,
            "has_default": bool(item["default"]["present"]),
        }
        if kind == "choice":
            field["choices"] = list(schema["enum"])
        if kind == "file":
            spec = _file_specs(ir).get(str(item["name"]))
            if spec is not None:
                field["accept"] = list(spec.accept)
                field["max_size"] = spec.max_size
        for key in ("minimum", "maximum"):
            bound = schema.get(key)
            if isinstance(bound, int | float) and not isinstance(bound, bool):
                field[key] = bound
        if kind == "json":
            field["json_type"] = (
                schema.get("type") if schema.get("type") in {"array", "object"} else "value"
            )
        # A secret input's default stays on the computer; leaving it blank uses it.
        if field["has_default"] and not secret:
            value = item["default"]["value"]
            if len(json.dumps(value)) <= DEFAULT_LIMIT:
                field["default"] = value
        fields.append(field)
    return fields


def _file_specs(ir: Any) -> dict[str, Any]:
    from gofer.rattish.file_inputs import file_input_specs

    return file_input_specs(ir) if isinstance(ir, dict) else {}


def _file_inputs(ir: Any) -> list[dict[str, Any]]:
    """The inputs a shared file can fill, with the kinds and size each takes."""
    return [
        {
            "name": field["name"],
            "label": field["label"],
            "accept": field.get("accept", []),
            "max_size": field.get("max_size", 0),
        }
        for field in _input_fields(ir)
        if field["kind"] == "file"
    ]


def _text_inputs(ir: Any) -> list[dict[str, str]]:
    """The inputs shared text can fill: plain text, not a choice and not a password."""
    return [
        {"name": field["name"], "label": field["label"]}
        for field in _input_fields(ir)
        if field["kind"] == "text" and not field["secret"]
    ]


def _unfilled_reason(names: Any, action: str) -> str:
    """Why the phone can't Resume or Run again: password inputs it can't fill, or ''."""
    shown = [_text(name, 128) for name in (names or [])[:5]]
    if not shown:
        return ""
    listed = ", ".join(shown) + (" and more" if len(names) > 5 else "")
    one = len(names) == 1
    return (
        f"This run's {'input' if one else 'inputs'} {listed} "
        f"{'is a password' if one else 'are passwords'}, which Raticode doesn't save, and "
        f"this phone can't fill {'it' if one else 'them'} in. Use {action} on your computer."
    )


def _depth(value: Any, level: int = 0) -> int:
    if level > INPUT_DEPTH:
        return level
    if isinstance(value, dict):
        return max((_depth(item, level + 1) for item in value.values()), default=level)
    if isinstance(value, list):
        return max((_depth(item, level + 1) for item in value), default=level)
    return level


def _missing_secrets_reason(names: Any) -> str:
    """Run's reason when secrets are missing. Values stay on the computer, so say where."""
    shown = [_text(name, 128) for name in names[:5]]
    first = shown[0]
    listed = ", ".join(shown) + (" and more" if len(names) > 5 else "")
    noun = "secret" if len(names) == 1 else "secrets"
    return (
        f"This workflow needs the workflow {noun} {listed}. On your computer, add "
        f"{'it' if len(names) == 1 else 'them'} in Workflow settings > Secrets or with "
        f"Raticode rattish secrets set {first}."
    )


def _provider_lines(record: dict[str, Any]) -> list[str]:
    """Why a model step switched providers and which one answered, for the node's line."""
    from gofer.rattish.provider_fallback import provider_label, switch_message

    switches = [item for item in record.get("fallbacks") or [] if isinstance(item, dict)]
    if not switches:
        return []
    lines = []
    for item in switches:
        try:
            lines.append(switch_message(item))
        except (KeyError, TypeError):
            continue
    provider = record.get("provider")
    if record.get("status") == "success" and isinstance(provider, dict):
        try:
            lines.append(f"Answered by {provider_label(provider)}.")
        except KeyError:
            pass
    return lines


def _modified(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
    except OSError:
        return ""


def gather(
    op: str,
    params: dict[str, Any],
    base: Path | None,
    projects: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Read workflows for a phone view. Runs outside the device lock.

    ``projects`` is the phone's open projects; the all-runs list reads only theirs.
    """
    from gofer.rattish.workspaces import list_registered_workflows
    from gofer.ui.api import (
        WorkflowLogError,
        WorkflowPlanError,
        rattish_rerun_state,
        rattish_resume_state_payload,
        recent_rattish_runs_payload,
        workflow_plan_payload,
        workflow_run_log_payload,
    )

    if base is None:
        raise Refused("unavailable", "Workflows are not available on this desktop.")
    if op == "workflows.runs":
        return _gather_runs(params, base, projects or {})
    registered = list_registered_workflows(registry_dir=base)
    if op in {"workflows.tests", "workflows.tests.estimate"}:
        from gofer.devices import workflow_tests

        identifier = _text(params.get("workflow_id"), 200)
        workflow = next((w for w in registered if w.workflow_id == identifier), None)
        if workflow is None:
            raise KeyError(identifier)
        return workflow_tests.gather(op, params, workflow, base)
    if op == "workflows.trigger.changes":
        return _gather_changes(params, registered, base)
    if op == "workflows.failure":
        return _gather_failure(params, registered, base)
    if op == "workflows.list":
        items = []
        for entry in registered:
            items.append(
                {
                    "registered": entry,
                    "ir": _compile(entry, base),
                    "runs": recent_rattish_runs_payload(entry.workflow_id, base, limit=1),
                }
            )
        return {"workflows": items, "triggers": _gather_pause(base)}
    identifier = _text(params.get("workflow_id"), 200)
    workflow = next((w for w in registered if w.workflow_id == identifier), None)
    if workflow is None:
        raise KeyError(identifier)
    ir = _compile(workflow, base)
    runs = recent_rattish_runs_payload(identifier, base, limit=RUN_LIMIT)
    plan = None
    if ir is not None:
        try:
            plan = workflow_plan_payload(identifier, base)
        except WorkflowPlanError:
            plan = None
    shown = _text(params.get("run_id"), 128) or next((str(run["id"]) for run in runs["runs"]), "")
    detail = None
    if shown:
        try:
            detail = workflow_run_log_payload(identifier, shown, base)
        except WorkflowLogError:
            if params.get("run_id"):
                raise KeyError(shown) from None
    # Whether the shown run, which the phone's Resume button names, can be resumed.
    # Read after the detail, so it is never older than the status the phone shows.
    resume = None
    if detail is not None:
        try:
            resume = rattish_resume_state_payload(identifier, shown, base)
        except WorkflowLogError:
            resume = None
    # Whether the shown run can be run again with its saved inputs.
    document = (detail or {}).get("rattishRun")
    rerun = rattish_rerun_state(document) if isinstance(document, dict) else None
    return {
        "registered": workflow,
        "ir": ir,
        "compileError": _compile_error(workflow, base) if ir is None else "",
        "runs": runs,
        "plan": plan,
        "detail": detail,
        "resume": resume,
        "rerun": rerun,
        "triggers": _gather_triggers(workflow.entrypoint, base),
    }


def _gather_failure(params: dict[str, Any], registered: Any, base: Path) -> dict[str, Any]:
    from gofer.ui.run_failure import RunFailureError, run_failure_payload

    identifier = _text(params.get("workflow_id"), 200)
    workflow = next((w for w in registered if w.workflow_id == identifier), None)
    if workflow is None:
        raise KeyError(identifier)
    run_id = _text(params.get("run_id"), 128)
    try:
        failure = run_failure_payload(
            run_id,
            base,
            workflow_id=identifier,
            node_id=_text(params.get("node_id"), 200) or None,
        )
    except RunFailureError:
        raise Refused("not_found", NO_FAILURE) from None
    return {"registered": workflow, "failure": failure}


def _gather_changes(
    params: dict[str, Any], registered: Sequence[Any], base: Path
) -> dict[str, Any]:
    """A trigger's changes since its approved version. Compiles, so it runs unlocked."""
    from gofer.rattish.triggers import TriggerNotFound, TriggerStore, trigger_changes

    identifier = _text(params.get("workflow_id"), 200)
    workflow = next((w for w in registered if w.workflow_id == identifier), None)
    if workflow is None:
        raise KeyError(identifier)
    store = TriggerStore(base)
    trigger_id = _text(params.get("trigger_id"), 64)
    try:
        record = store.get(trigger_id)
    except TriggerNotFound:
        raise Refused("not_found", NO_TRIGGER) from None
    if _same(record.source_path) != _same(workflow.entrypoint):
        raise Refused("not_found", NO_TRIGGER)
    return {"registered": workflow, "changes": trigger_changes(store, trigger_id)}


def _gather_pause(base: Path) -> dict[str, Any]:
    from gofer.rattish.triggers import TriggerStore, computer_limit_state

    store = TriggerStore(base)
    triggers = store.list_triggers()
    return {
        "pause": store.pause_state(),
        "count": len(triggers),
        "enabled": sum(1 for item in triggers if item.enabled),
        "daily": computer_limit_state(store),
    }


def _gather_triggers(entrypoint: Path, base: Path) -> dict[str, Any]:
    """One workflow's triggers with their last results, and whether all are paused."""
    from gofer.rattish.triggers import (
        TriggerStore,
        approval_status,
        next_run_at,
        trigger_limit_state,
    )

    store = TriggerStore(base)
    items = []
    snapshots: dict[str, Any] = {}
    for record in store.list_triggers(entrypoint)[:TRIGGER_LIMIT]:
        items.append(
            {
                "record": record,
                "next_run_at": next_run_at(record),
                "latest": store.latest_event(record.id),
                "daily": trigger_limit_state(store, record),
                "approval": approval_status(store, record, snapshots=snapshots).state,
            }
        )
    return {"items": items, "pause": store.pause_state()}


def _time_limit_text(seconds: int | None) -> str:
    """A trigger's Time limit as the phone shows it."""
    from gofer.rattish.trigger_defaults import format_run_time

    return f"Time limit: {format_run_time(seconds)}" if seconds else "No time limit"


def _daily_fields(state: Any) -> dict[str, Any]:
    """A daily limit as the phone shows it: today's use, and whether starts are skipped."""
    if state is None:
        return {"daily_limit": "", "limit_reached": False}
    text = f"Daily limit: {state.used_text()}"
    if state.reached:
        text += f". Triggered starts are skipped until midnight ({state.timezone})"
    return {"daily_limit": _text(text + ".", 300), "limit_reached": bool(state.reached)}


def _gather_runs(
    params: dict[str, Any], base: Path, projects: dict[str, dict[str, str]]
) -> dict[str, Any]:
    from gofer.ui.workflow_runs import WorkflowRunsError, all_workflow_runs_payload

    span = _text(params.get("since"), 8)
    status = _text(params.get("status"), 20) or None
    workflow = _text(params.get("workflow_id"), 200)
    try:
        return all_workflow_runs_payload(
            base,
            status=status,
            since=span if span in RUN_SPANS else None,
            workflow_ids=[workflow] if workflow else None,
            project_roots=[project["root"] for project in projects.values()],
            limit=ALL_RUNS_LIMIT,
        )
    except WorkflowRunsError as exc:
        raise Refused("invalid", str(exc)) from None


class PhoneWorkflows:
    def __init__(self, control: Any) -> None:
        self.control = control
        self.db = control.db
        self.db.execute(
            # Which run each phone request started, so a resent request starts none.
            "CREATE TABLE IF NOT EXISTS device_workflow_requests (peer TEXT NOT NULL, "
            "request TEXT NOT NULL, workflow TEXT NOT NULL, run TEXT, "
            "PRIMARY KEY(peer,request))"
        )

    @property
    def base(self) -> Path | None:
        data_dir = self.control.app.remote.data_dir
        return Path(data_dir) if data_dir is not None else None

    def available(self) -> bool:
        return self.base is not None

    # Scope -------------------------------------------------------------------

    def _open_projects(self, peer: str) -> dict[str, str]:
        return {
            _same(project["root"]): project["name"]
            for project in self.control.projects(peer).values()
        }

    def _scoped(self, peer: str, registered: Any) -> str:
        """The open project's name, or a refusal when the phone can't see this workflow."""
        name = self._open_projects(peer).get(_same(registered.project_root))
        if name is None:
            raise Refused("not_found", NOT_OPEN)
        return name

    def _clean(self, value: Any, registered: Any, project: str, limit: int) -> str:
        return self._hide_folders(_text(value, 4000), registered.project_root, project)[:limit]

    def _hide_folders(self, text: str, root: Any, project: str) -> str:
        """Names the project and Raticode's data folder instead of their paths."""
        base = self.base
        for folder, label in ((str(root), project), (str(base) if base else "", "Raticode data")):
            if folder:
                text = text.replace(folder, label)
        return text

    # Reads -------------------------------------------------------------------

    def read(self, peer: str, op: str, params: dict[str, Any], data: dict[str, Any]) -> Any:
        if op == "workflows.tests":
            return self._tests_view(peer, data["registered"], data["tests"])
        if op == "workflows.tests.estimate":
            from gofer.devices import workflow_tests

            self._scoped(peer, data["registered"])
            return workflow_tests.estimate_view(data["registered"].workflow_id, data["estimate"])
        if op == "workflows.runs":
            return self._all_runs(peer, data)
        if op == "workflows.trigger.changes":
            return self._changes(peer, data)
        if op == "workflows.failure":
            failed_workflow = data["registered"]
            failed_project = self._scoped(peer, failed_workflow)
            failure = data["failure"]
            return {
                "run_id": _text(failure["runId"], 128),
                "node_id": _text(failure["nodeId"], 200),
                "name": _text(failure["name"], 200),
                "text": self._clean_long(failure["text"], failed_workflow, failed_project),
            }
        if op == "workflows.list":
            projects = self._open_projects(peer)
            items = []
            for item in data["workflows"]:
                project = projects.get(_same(item["registered"].project_root))
                if project is not None:
                    items.append(self._summary(item, project))
            items.sort(key=self._recency, reverse=True)
            return {
                "workflows": items[:LIST_LIMIT],
                "earlier": max(0, len(items) - LIST_LIMIT),
                "triggers": self._pause_summary(data["triggers"]),
            }
        registered = data["registered"]
        project = self._scoped(peer, registered)
        summary = self._summary(data, project)
        ir = data["ir"]
        detail = data["detail"]
        if ir is None and detail is not None:
            # A workflow that no longer compiles still shows the graph it last ran.
            ir = (detail.get("rattishRun") or {}).get("compiled_snapshot")
        nodes, edges = self._graph(ir)
        runs = [self._row(run, registered.project_root, project) for run in data["runs"]["runs"]]
        waiting = data["runs"]["awaitingNodes"]
        plan = data["plan"]
        shown = None
        if detail is not None:
            listed = {str(run.get("id")): run for run in data["runs"]["runs"]}
            usage_wait = (listed.get(str(detail.get("runId") or "")) or {}).get("usageLimitWait")
            shown = self._detail(detail, waiting, registered, project, nodes, usage_wait)
            # The detail was read last. A run that ended in between must not
            # show as both running and finished in one answer.
            fresh = {key: value for key, value in shown.items() if key not in {"error", "nodes"}}
            error = self._message(detail.get("error"))
            row_error = self._run_error(error, registered.project_root, project)
            fresh.update(self._error_field(fresh["status"], row_error))
            runs = [fresh if run["run_id"] == shown["run_id"] else run for run in runs]
            if runs and runs[0]["run_id"] == shown["run_id"]:
                summary["latest_run"] = fresh
                if summary["status"] != "invalid":
                    summary["status"] = fresh["status"]
        return {
            **summary,
            "revision": (data["ir"] or {}).get("source", {}).get("source_fingerprint", ""),
            "nodes": nodes,
            "edges": edges,
            "runs": runs,
            "earlier_runs": max(0, data["runs"]["total"] - len(runs)),
            "run": shown,
            "preview": self._preview(plan, registered, project),
            "inputs": _input_fields(data["ir"]),
            "triggers": [
                self._trigger(item, registered, project) for item in data["triggers"]["items"]
            ],
            "triggers_paused": data["triggers"]["pause"].paused,
            "controls": self._controls(
                data["ir"],
                plan,
                runs,
                data.get("resume"),
                form=params.get("input_form") is True,
                rerun=data.get("rerun"),
                problem=self._clean(
                    data.get("compileError")
                    or ((plan or {}).get("blockingDiagnostics") or [""])[0],
                    registered,
                    project,
                    300,
                ),
            ),
        }

    def _all_runs(self, peer: str, data: dict[str, Any]) -> dict[str, Any]:
        """Every open project's workflow runs, newest first, with totals for the filter."""
        from gofer.rattish.provider_fallback import switched

        projects = self._open_projects(peer)
        runs = []
        for run in data["runs"]:
            project = projects.get(_same(run["projectRoot"]))
            if project is None:
                continue  # Closed on the desktop after the read.
            usage = run.get("usage") or {}
            shown = self._row(run, run["projectRoot"], project)
            if switched(run.get("fallbacks")):
                # The phone shows this line under each run; the run detail names the providers.
                shown["summary"] = f"{shown['summary']} · switched provider"
            runs.append(
                {
                    **shown,
                    "workflow_id": _text(run.get("workflowId"), 200),
                    "workflow_name": _text(run.get("workflowName"), 200),
                    "project_name": _text(project, 200),
                    "tokens": _text(usage.get("tokensLabel") or "", 80),
                    "cost": _text(usage.get("costLabel") or "", 80),
                }
            )
        totals = data["totals"]
        return {
            "runs": runs,
            "earlier": max(0, int(data["pagination"]["total"]) - len(data["runs"])),
            "summary": _text(totals.get("summary"), 300),
            "workflows": [
                {"workflow_id": _text(w["id"], 200), "name": _text(w["name"], 200)}
                for w in data["workflows"]
            ][:LIST_LIMIT],
        }

    @staticmethod
    def _pause_summary(data: dict[str, Any]) -> dict[str, Any]:
        pause = data["pause"]
        return {
            "paused": pause.paused,
            "paused_at": _text(pause.since if pause.paused else "", 80),
            "count": int(data["count"]),
            "enabled": int(data["enabled"]),
            **_daily_fields(data.get("daily")),
        }

    def _trigger(self, item: dict[str, Any], registered: Any, project: str) -> dict[str, Any]:
        """A trigger as the phone lists it. A webhook's address and token stay here."""
        from gofer.rattish.triggers import describe_trigger

        record = item["record"]
        config = record.config
        if record.kind == "cron":
            description = describe_trigger(record)
        elif record.kind == "file":
            pattern = "" if config.get("glob") in {None, "*"} else f" ({config.get('glob')})"
            description = f"{config.get('path')}{pattern}"
        else:
            description = "Local webhook on your computer"
        latest = item["latest"]
        return {
            "trigger_id": _text(record.id, 64),
            "kind": _text(record.kind, 20),
            "kind_label": TRIGGER_KINDS.get(record.kind, _text(record.kind, 20)),
            "label": _text(record.label, 120),
            "description": self._clean(description, registered, project, 300),
            "enabled": record.enabled,
            # approved, changed, unapproved or missing; the message says what to do.
            "approval": _text(item.get("approval") or "approved", 20),
            "approval_message": ""
            if record.run_unapproved
            else APPROVAL_LINES.get(str(item.get("approval")), ""),
            "run_edits_without_approval": record.run_unapproved,
            "next_run_at": _text(item["next_run_at"], 80),
            **_daily_fields(item.get("daily")),
            "time_limit": _time_limit_text(record.max_run_seconds),
            "last_result": None
            if latest is None
            else {
                "status": _text(latest.status, 20),
                "at": _text(latest.at, 80),
                "message": self._clean(latest.message, registered, project, 300),
            },
        }

    def _changes(self, peer: str, data: dict[str, Any]) -> dict[str, Any]:
        """Review changes for one trigger: what differs from the version the user approved.

        Each file carries its own unified diff, cut so all of them together stay under
        ``DIFF_LIMIT`` characters.
        """
        registered = data["registered"]
        project = self._scoped(peer, registered)
        changes = data["changes"]
        files, truncated = self._phone_files(changes, registered, project)
        project_id = next(
            (
                key
                for key, value in self.control.projects(peer).items()
                if _same(value["root"]) == _same(registered.project_root)
            ),
            "",
        )
        message = APPROVAL_LINES.get(changes["state"], "")
        marked = changes.get("changedDuringRun")
        if marked and marked.get("path"):
            # The file may have changed back since, so the list can be empty.
            shown = self._clean(str(marked["path"]), registered, project, 300)
            message = f"{message} {shown} {CHANGED_DURING_RUN}".strip()
        return {
            "workflow_id": registered.workflow_id,
            "trigger_id": _text(changes["triggerId"], 64),
            "approval": _text(changes["state"], 20),
            "message": message,
            "fingerprint": _text(changes["fingerprint"], 80),
            "approved_at": _text(changes.get("approvedAt") or "", 80),
            "project_id": _text(project_id, 128),
            # Files past NODE_LIMIT aren't listed; their changes count as unseen.
            "files": [
                {key: value for key, value in item.items() if key != "hidden"}
                for item in files
                if not item["hidden"]
            ],
            "unseen_hidden": sum(1 for item in files if item["hidden"] and item["unseen"]),
            "truncated": truncated,
        }

    def _phone_files(
        self, changes: dict[str, Any], registered: Any, project: str
    ) -> tuple[list[dict[str, Any]], bool]:
        """Each changed file as the phone shows it, and whether any diff was cut.

        Each diff is cut so all of them together stay under ``DIFF_LIMIT`` characters.
        ``unseen`` is ``cut`` or ``unreadable`` when the phone can't show the whole change,
        or ``unpinned`` for an MCP server whose package can change between runs (see
        ``trigger_approval.unseen_reason``), else empty. ``warning`` is the line the phone
        shows on such an entry. ``open_path`` is the file's
        path inside the project for ``files.text``, empty for one outside it. Files past
        ``NODE_LIMIT`` are marked ``hidden``.
        """
        from gofer.rattish.trigger_approval import unseen_reason

        root = Path(registered.project_root).resolve()
        budget = DIFF_LIMIT
        truncated = bool(changes.get("truncated"))
        files = []
        for index, item in enumerate(changes["files"]):
            hidden = index >= NODE_LIMIT
            diff = "" if hidden else self._clean_long(item["diff"], registered, project)
            cut = len(diff) > budget
            diff = diff[: max(budget, 0)]
            budget -= len(diff)
            truncated = truncated or cut
            shown = {
                "path": self._clean(item["path"], registered, project, 300),
                "kind": _text(item["roleLabel"], 40),
                "change": _text(item["change"], 20),
                "diff": diff,
                "diff_available": bool(item["diffAvailable"]),
                "truncated": bool(item["truncated"]) or cut or hidden,
                # An MCP server whose package has no exact version: the warning to show.
                "warning": _text(item.get("warning") or "", 200),
            }
            # An MCP server's definition has no file, so no absolute path to open.
            absolute = str(item.get("absolutePath") or "")
            try:
                inside = Path(absolute).resolve().relative_to(root) if absolute else Path()
                open_path = inside.as_posix() if inside.parts else ""
            except (OSError, ValueError):
                open_path = ""
            files.append(
                {
                    **shown,
                    "unseen": unseen_reason(
                        {
                            **shown,
                            "diffAvailable": shown["diff_available"],
                            "unpinned": item.get("unpinned"),
                        }
                    )
                    or "",
                    "open_path": _text(open_path, 1000),
                    "hidden": hidden,
                }
            )
        return files, truncated

    def _clean_long(self, value: str, registered: Any, project: str) -> str:
        """Like ``_clean`` for text longer than its 4000-character cut."""
        return self._hide_folders(value[: DIFF_LIMIT + 1], registered.project_root, project)

    @staticmethod
    def _recency(item: dict[str, Any]) -> str:
        latest = item.get("latest_run") or {}
        return max(str(latest.get("started_at") or ""), str(item.get("updated_at") or ""))

    def _summary(self, item: dict[str, Any], project: str) -> dict[str, Any]:
        registered = item["registered"]
        ir = item["ir"]
        runs = item["runs"]["runs"]
        latest = self._row(runs[0], registered.project_root, project) if runs else None
        if ir is None:
            status, description = "invalid", INVALID
        else:
            nodes = ir["nodes"]
            routes = sum(len(node["routes"]) for node in nodes)
            status = latest["status"] if latest else "idle"
            description = f"{_count(len(nodes), 'node')}, {_count(routes, 'route')}."
        if not registered.managed:
            # Found outside .raticode: the folder tells same-named workflows apart.
            description = f"{registered.relative_folder} · {description}"
        return {
            "workflow_id": _text(registered.workflow_id, 200),
            "name": _text((ir or {}).get("workflow", {}).get("name") or registered.name, 200),
            "description": _text(description, 500),
            "project_name": project,
            "status": status,
            "updated_at": _modified(registered.entrypoint),
            "latest_run": latest,
            "text_inputs": _text_inputs(ir),
            "file_inputs": _file_inputs(ir),
        }

    def _row(self, run: dict[str, Any], root: Any, project: str) -> dict[str, Any]:
        """A run list's row, with a failed run's error naming folders like the rest."""
        return self._run(run, self._run_error(run.get("error"), root, project))

    def _run_error(self, value: Any, root: Any, project: str) -> str:
        text = self._hide_folders(_text(value, RUN_ERROR_LIMIT), root, project)
        # Run lists keep the first 300 characters, which can end partway through a folder's
        # path, after the whole-path names above no longer match it.
        for folder in (str(root), str(self.base) if self.base else ""):
            for length in range(min(len(folder) - 1, len(text)), 1, -1):
                if text.endswith(folder[:length]):
                    return text[:-length] + "…"
        return text

    @staticmethod
    def _message(error: Any) -> Any:
        """A run record's error message; older records keep it as plain text."""
        return error.get("message") if isinstance(error, dict) else error

    @staticmethod
    def _error_field(status: str, error: str) -> dict[str, str]:
        """Why a run stopped, for the rows of runs that ended badly; nothing for the rest."""
        if status in ENDED_WITH_ERROR and error:
            return {"error": error[:RUN_ERROR_LIMIT]}
        return {}

    @staticmethod
    def _run(run: dict[str, Any], error: str = "") -> dict[str, Any]:
        status = RUN_STATUS.get(str(run.get("status")), _text(run.get("status"), 40))
        if run.get("stopRequested") and status == "running":
            status = "stopping"
        elif run.get("awaitingApproval") and status == "running":
            status = "waiting"
        seconds = float(run.get("durationSeconds") or 0)
        if status == "stopping":
            summary = "Stop requested"
        elif status == "waiting":
            summary = "Waiting for approval"
        elif status == "failed" and run.get("usageLimitWait"):
            # A triggered run the scheduler resumes once the provider's limit resets.
            from gofer.rattish.triggers import USAGE_WAIT_LABEL

            summary = USAGE_WAIT_LABEL
        elif status == "running":
            summary = "Running"
        else:
            summary = f"Took {_duration(seconds)}"
        return {
            "run_id": _text(run.get("id"), 128),
            "status": status,
            "started_at": _text(run.get("startedAt"), 80),
            "finished_at": _text(run.get("finishedAt"), 80),
            "trigger": _text(run.get("triggerType"), 40),
            "started_by": _text(run.get("startedBy"), 20),
            "summary": summary,
            "duration_seconds": seconds,
            "resumed_from": _text(run.get("resumedFrom"), 128),
            **PhoneWorkflows._error_field(status, error),
        }

    @staticmethod
    def _graph(ir: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not isinstance(ir, dict):
            return [], []
        nodes = []
        edges = []
        linked: set[tuple[str, str]] = set()
        for node in ir.get("nodes") or []:
            identifier = _text(node.get("id"), 200)
            nodes.append(
                {"node_id": identifier, "label": identifier, "kind": _text(node.get("type"), 80)}
            )
            for route in node.get("routes") or []:
                target = _text(route.get("target"), 200)
                linked.add((identifier, target))
                edges.append(
                    {
                        "from": identifier,
                        "to": target,
                        "label": EDGE_LABELS.get(str(route.get("mode")), ""),
                    }
                )
        for node in ir.get("nodes") or []:
            # A node can wait on another without a route from it; draw that too.
            for need in (node.get("readiness") or {}).get("needs") or []:
                pair = (_text(need, 200), _text(node.get("id"), 200))
                if pair not in linked:
                    linked.add(pair)
                    edges.append({"from": pair[0], "to": pair[1], "label": "needs"})
        return nodes[:NODE_LIMIT], edges[:EDGE_LIMIT]

    def _detail(
        self,
        detail: dict[str, Any],
        waiting: dict[str, list[str]],
        registered: Any,
        project: str,
        graph: list[dict[str, Any]],
        usage_wait: Any = None,
    ) -> dict[str, Any]:
        run_id = str(detail.get("runId") or "")
        document = detail.get("rattishRun") or {}
        run = self._run(
            {
                "id": run_id,
                "status": detail.get("status"),
                "awaitingApproval": bool(waiting.get(run_id)),
                "stopRequested": bool(detail.get("stopRequested")),
                "startedAt": document.get("started_at"),
                "finishedAt": None
                if document.get("status") == "running"
                else document.get("finished_at"),
                "durationSeconds": float(document.get("duration_ms") or 0) / 1000,
                "triggerType": "manual",
                "startedBy": document.get("started_by")
                or ("trigger" if document.get("trigger_event_count") else "manual"),
                "resumedFrom": detail.get("resumedFrom"),
                "usageLimitWait": usage_wait,
            }
        )
        active = run["status"] in {"running", "waiting", "stopping"}
        records = detail.get("runNodes") or {}
        nodes = []
        for node in graph:
            record = records.get(node["node_id"])
            if node["node_id"] in waiting.get(run_id, []):
                status, line = "waiting", "Waiting for approval"
            elif record is None:
                status, line = ("pending" if active else "skipped"), ""
            else:
                raw = str(record.get("status"))
                status = NODE_STATUS.get(raw, _text(raw, 40))
                line = self._clean(
                    " ".join(
                        part
                        for part in (
                            f"Provider: {record['providerApp']}."
                            if record.get("providerApp")
                            else "",
                            *_provider_lines(record),
                            record.get("message")
                            or ("Reused from the earlier run" if record.get("reused") else "")
                            or (
                                "Cached result: no model call, 0 tokens, $0"
                                if record.get("cached")
                                else ""
                            )
                            or record.get("activationSummary")
                            or ("Failed, allowed to continue" if raw == "allowed_failure" else ""),
                        )
                        if part
                    ),
                    registered,
                    project,
                    300,
                )
            nodes.append(
                {
                    "node_id": node["node_id"],
                    "status": status,
                    "started_at": _text((record or {}).get("startedAt"), 80),
                    "finished_at": _text((record or {}).get("finishedAt"), 80),
                    "detail": line,
                }
            )
        return {
            **run,
            "error": self._clean(self._message(detail.get("error")), registered, project, 600),
            "nodes": nodes,
        }

    def _preview(self, plan: Any, registered: Any, project: str) -> dict[str, Any]:
        """What the desktop's run preview shows before Run, for the phone's confirmation."""
        plan = plan or {}

        def clean(items: Any) -> list[str]:
            return [self._clean(item, registered, project, 300) for item in (items or [])[:20]]

        return {
            "blocking": clean(plan.get("blockingDiagnostics")),
            "destructive": clean(plan.get("destructiveActions")),
            "warnings": clean(plan.get("warnings")),
        }

    def _controls(
        self,
        ir: Any,
        plan: Any,
        runs: list[dict[str, Any]],
        resume: dict[str, Any] | None = None,
        *,
        form: bool = False,
        rerun: dict[str, Any] | None = None,
        problem: str = "",
    ) -> dict[str, Any]:
        """``form`` is True for a phone that shows the Run form and can supply inputs.

        The phone never sends inputs with Resume or Run again, so a run whose password
        inputs weren't saved names them and says to use the computer instead.
        ``problem`` is the first compile error or preflight error, which the Run reason
        names so the phone says what to fix, as the desktop does.
        """

        def control(reason: str) -> dict[str, Any]:
            return {"allowed": not reason, "reason": reason}

        if ir is None:
            run = (
                f"This workflow has an error. {problem} Fix it on your computer."
                if problem
                else INVALID
            )
        elif plan is None:
            run = "Your computer couldn't prepare this workflow. Open it there to check it."
        elif plan.get("requiredSecrets"):
            run = _missing_secrets_reason(plan["requiredSecrets"])
        elif plan.get("blockingDiagnostics"):
            run = (
                f"This workflow can't run yet: {problem}"
                if problem
                else "This workflow can't run yet. Open it on your computer to see why."
            )
        elif not plan.get("runnable"):
            run = "This workflow has no nodes to run."
        elif _missing_inputs(ir) and not form:
            run = NEEDS_INPUTS
        else:
            run = ""
        active = any(r["status"] in {"running", "waiting", "stopping"} for r in runs)
        if resume is None:
            resumable = control(NO_RUN)
        else:
            reason = str(resume["reason"]) or NO_RESUME
            if resume["allowed"]:
                reason = _unfilled_reason(resume.get("needsInputs"), "Resume")
            resumable = control(reason)
            # Steps a resume runs again, for the phone's confirmation.
            resumable["rerun_nodes"] = (
                [_text(node, 200) for node in resume.get("rerunNodes") or []][:NODE_LIMIT]
                if not reason
                else []
            )
        if rerun is None:
            again = NO_RERUN
        elif not rerun["allowed"]:
            again = str(rerun["reason"]) or NO_RERUN
        elif run and run != NEEDS_INPUTS:
            # Whatever stops a new run (errors, missing secrets) stops this one too.
            again = run
        else:
            again = _unfilled_reason(rerun.get("needsInputs"), "Run again")
        return {
            "run": control(run),
            "stop": control("" if active else NOT_RUNNING),
            # About the shown run, which is the one the phone's Resume button names.
            "resume": resumable,
            # Run the workflow again with the shown run's saved inputs and events.
            "rerun": control(again),
        }

    def _tests_view(
        self, peer: str, registered: Any, payload: dict[str, Any], message: str = ""
    ) -> dict[str, Any]:
        from gofer.devices import workflow_tests

        project = self._scoped(peer, registered)
        workspace = self.control.app.workspace
        workspace.refresh_providers()
        view = workflow_tests.tests_view(
            registered,
            payload,
            list(workspace.providers or []),
            lambda value, limit: self._clean(value, registered, project, limit),
        )
        if message:
            view["message"] = message
        return view

    def _test_action(
        self, peer: str, registered: Any, op: str, params: dict[str, Any], request: str, base: Path
    ) -> dict[str, Any]:
        """Try another model, or use the tried one; answers with the refreshed tests view."""
        from gofer.devices import workflow_tests
        from gofer.ui.workflow_tests import workflow_tests_payload

        if op == "workflows.tests.use_model":
            message = workflow_tests.use_tried_model(registered, base, params)
            logger.info("Phone wrote a tried model into workflow %s", registered.workflow_id)
        else:
            prior = self.db.execute(
                "SELECT run FROM device_workflow_requests WHERE peer=? AND request=?",
                (peer, request),
            ).fetchone()
            if prior is not None:
                message = "This try was already started."
            else:
                result_id = workflow_tests.start_try(registered, base, params)
                self.db.execute(
                    "INSERT INTO device_workflow_requests VALUES (?,?,?,?)",
                    (peer, request, registered.workflow_id, result_id),
                )
                message = "Trying the model. Each case runs with both models."
        return self._tests_view(
            peer, registered, workflow_tests_payload(registered.workflow_id, base), message
        )

    # Actions -----------------------------------------------------------------

    def act(self, peer: str, op: str, params: dict[str, Any], request: str) -> dict[str, Any]:
        from gofer.rattish.workspaces import list_registered_workflows
        from gofer.ui.api import WorkflowRunError

        base = self.base
        if base is None:
            raise Refused("unavailable", "Workflows are not available on this desktop.")
        if op == "workflows.triggers.pause":
            return self._pause_triggers(params, base)
        identifier = _text(params.get("workflow_id"), 200)
        registered = next(
            (
                w
                for w in list_registered_workflows(registry_dir=base)
                if w.workflow_id == identifier
            ),
            None,
        )
        if registered is None:
            raise Refused("not_found", "This workflow no longer exists on desktop.")
        project = self._scoped(peer, registered)
        try:
            if op in {"workflows.tests.try", "workflows.tests.use_model"}:
                return self._test_action(peer, registered, op, params, request, base)
            if op == "workflows.run":
                return self._start(peer, registered, project, params, request, base)
            if op == "workflows.stop":
                return self._stop(registered, project, params, base)
            if op == "workflows.rerun":
                return self._rerun(peer, registered, params, request, base)
            if op == "workflows.trigger":
                return self._set_trigger(registered, params, base)
            if op == "workflows.trigger.approve":
                return self._approve_trigger(registered, project, params, base)
            return self._resume(peer, registered, params, request, base)
        except WorkflowRunError as exc:
            message = str(exc)
            if "source changed" in message:
                raise Refused("conflict", CHANGED) from None
            raise Refused("invalid", self._clean(message, registered, project, 1000)) from None

    def _set_trigger(self, registered: Any, params: dict[str, Any], base: Path) -> dict[str, Any]:
        """Turn one of this workflow's triggers on or off. On needs the phone's confirmation."""
        from gofer.rattish.triggers import (
            TriggerError,
            TriggerNotFound,
            TriggerStore,
            approval_status,
        )

        enabled = params.get("enabled")
        if not isinstance(enabled, bool):
            raise Refused("invalid", "Choose on or off.")
        if enabled and params.get("confirmed") is not True:
            raise Refused("invalid", CONFIRM_TRIGGER)
        store = TriggerStore(base)
        trigger_id = _text(params.get("trigger_id"), 64)
        try:
            record = store.get(trigger_id)
        except TriggerNotFound:
            raise Refused("not_found", NO_TRIGGER) from None
        if _same(record.source_path) != _same(registered.entrypoint):
            raise Refused("not_found", NO_TRIGGER)
        try:
            record = store.update(trigger_id, enabled=enabled, actor="user")
        except TriggerNotFound:
            raise Refused("not_found", NO_TRIGGER) from None
        except TriggerError as exc:
            raise Refused("invalid", _text(exc, 600)) from None
        # Turning a trigger on approves nothing: a change waiting for review stays waiting.
        approval = approval_status(store, record).state if enabled else "approved"
        waiting = enabled and approval in APPROVAL_LINES and not record.run_unapproved
        if not enabled:
            message = "Turned off. Its settings and history are kept."
        elif waiting:
            message = (
                f"Turned on. {APPROVAL_LINES[approval]} Review the changes: "
                "triggered starts are skipped until you approve them."
            )
        elif store.pause_state().paused:
            message = "Turned on. All triggers are paused, so it starts when you resume them."
        else:
            message = "Turned on."
        logger.info("Phone turned trigger %s %s", record.id, "on" if enabled else "off")
        return {
            "workflow_id": registered.workflow_id,
            "trigger_id": record.id,
            "enabled": record.enabled,
            "approval": approval if waiting else "approved",
            "message": message,
        }

    def _approve_trigger(
        self, registered: Any, project: str, params: dict[str, Any], base: Path
    ) -> dict[str, Any]:
        """Approve this version: the exact one the phone showed, by its fingerprint.

        When the phone couldn't show a file's whole change, it asks a second time and
        sends ``unseen_confirmed``; without that nothing is approved.
        """
        from gofer.rattish.trigger_approval import unseen_list_text
        from gofer.rattish.triggers import (
            ApprovalConflict,
            TriggerError,
            TriggerNotFound,
            TriggerStore,
            UnseenChanges,
            approve_trigger,
        )

        def unseen_on_phone(changes: dict[str, Any]) -> list[dict[str, Any]]:
            files, _ = self._phone_files(changes, registered, project)
            return [
                {"path": item["path"], "reason": item["unseen"]} for item in files if item["unseen"]
            ]

        fingerprint = _text(params.get("fingerprint"), 80)
        if not fingerprint:
            raise Refused("invalid", REVIEW_FIRST)
        store = TriggerStore(base)
        trigger_id = _text(params.get("trigger_id"), 64)
        try:
            record = store.get(trigger_id)
        except TriggerNotFound:
            raise Refused("not_found", NO_TRIGGER) from None
        if _same(record.source_path) != _same(registered.entrypoint):
            raise Refused("not_found", NO_TRIGGER)
        try:
            approve_trigger(
                store,
                trigger_id,
                actor="user",
                by="phone",
                expected_fingerprint=fingerprint,
                accept_unseen=params.get("unseen_confirmed") is True,
                unseen_in=unseen_on_phone,
            )
        except ApprovalConflict:
            raise Refused("conflict", CHANGED_AGAIN) from None
        except UnseenChanges as exc:
            shown = _text(unseen_list_text(exc.files), 1200)
            raise Refused("invalid", UNSEEN_FIRST.format(files=shown)) from None
        except TriggerNotFound:
            raise Refused("not_found", NO_TRIGGER) from None
        except TriggerError as exc:
            raise Refused("invalid", _text(exc, 600)) from None
        logger.info("Phone approved the workflow version for trigger %s", trigger_id)
        return {
            "workflow_id": registered.workflow_id,
            "trigger_id": trigger_id,
            "approval": "approved",
            "message": "Approved. The trigger runs this version from now on.",
        }

    def _pause_triggers(self, params: dict[str, Any], base: Path) -> dict[str, Any]:
        """Pause or resume every trigger. Each keeps its own on/off setting."""
        from gofer.rattish.triggers import TriggerStore

        paused = params.get("paused")
        if not isinstance(paused, bool):
            raise Refused("invalid", "Choose pause or resume.")
        if not paused and params.get("confirmed") is not True:
            raise Refused("invalid", CONFIRM_RESUME)
        store = TriggerStore(base)
        store.set_paused(paused)
        summary = self._pause_summary(_gather_pause(base))
        if paused:
            message = "All triggers paused. Scheduled times are skipped until you resume."
        else:
            message = (
                f"Triggers resumed: {summary['enabled']} of {summary['count']} on, as each was set."
            )
        logger.info("Phone %s all triggers", "paused" if paused else "resumed")
        return {**summary, "message": message}

    def _answer(self, registered: Any, run_id: str, status: str, message: str) -> dict[str, Any]:
        return {
            "workflow_id": registered.workflow_id,
            "run_id": run_id,
            "status": status,
            "message": message,
        }

    def _status(self, registered: Any, run_id: str, base: Path) -> str:
        from gofer.ui.api import WorkflowLogError, workflow_run_log_payload

        try:
            payload = workflow_run_log_payload(
                registered.workflow_id, run_id, base, include_details=False
            )
        except WorkflowLogError:
            return "unknown"
        return RUN_STATUS.get(str(payload.get("status")), "unknown")

    def _start(
        self,
        peer: str,
        registered: Any,
        project: str,
        params: dict[str, Any],
        request: str,
        base: Path,
    ) -> dict[str, Any]:
        from gofer.ui.api import run_workflow_payload

        prior = self.db.execute(
            "SELECT run FROM device_workflow_requests WHERE peer=? AND request=?",
            (peer, request),
        ).fetchone()
        if prior is not None:
            run_id = prior[0] or ""
            status = self._status(registered, run_id, base) if run_id else "queued"
            return self._answer(registered, run_id, status, "This run was already started.")
        revision = params.get("expected_revision")
        if not isinstance(revision, str) or not revision:
            raise Refused("invalid", REFRESH)
        ir = _compile(registered, base)
        if ir is None:
            raise Refused("invalid", INVALID)
        if ir["source"]["source_fingerprint"] != revision:
            raise Refused("conflict", CHANGED)
        inputs = self._inputs(ir, params, registered, project)
        if "file_ids" in params:
            inputs.update(self._uploaded_files(peer, ir, params["file_ids"], base))
        # The same background start the desktop's Run button uses, with its
        # saved-revision check, active-run limit and preflight.
        started = settle(
            lambda: run_workflow_payload(
                registered.workflow_id,
                base,
                background=True,
                expected_revision=revision,
                inputs=inputs or None,
                started_by="phone",
            )
        )
        if started is None:
            self.db.execute(
                "INSERT INTO device_workflow_requests VALUES (?,?,?,NULL)",
                (peer, request, registered.workflow_id),
            )
            return self._answer(registered, "", "queued", STILL_STARTING)
        run_id = _text(started.get("runId"), 128)
        self.db.execute(
            "INSERT INTO device_workflow_requests VALUES (?,?,?,?)",
            (peer, request, registered.workflow_id, run_id),
        )
        status = RUN_STATUS.get(str(started.get("status")), "unknown")
        if status == "failed":
            error = started.get("error") or {}
            reason = self._clean(
                error.get("message") if isinstance(error, dict) else error,
                registered,
                project,
                600,
            )
            return self._answer(
                registered, run_id, status, f"The run couldn't start. {reason}".strip()
            )
        return self._answer(registered, run_id, status, "Started on your computer.")

    def _inputs(
        self, ir: dict[str, Any], params: dict[str, Any], registered: Any, project: str
    ) -> dict[str, Any]:
        """The phone's input values, checked against the workflow's input schema.

        A phone without the Run form sends no ``inputs``; it can only start a
        workflow whose inputs all have defaults.
        """
        from gofer.rattish.runtime import (
            InvalidRattishWorkflowInputError,
            _prepare_workflow_inputs,
        )

        if "inputs" not in params:
            if [name for name in _missing_inputs(ir) if name not in params.get("file_ids", {})]:
                raise Refused("invalid", NEEDS_INPUTS)
            return {}
        inputs = params["inputs"]
        if not isinstance(inputs, dict) or len(inputs) > INPUT_LIMIT:
            raise Refused("invalid", BAD_INPUTS)
        try:
            size = len(json.dumps(inputs, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError):
            raise Refused("invalid", BAD_INPUTS) from None
        if size > INPUTS_LIMIT or _depth(inputs) > INPUT_DEPTH:
            raise Refused("invalid", TOO_LARGE)
        declared = {item["name"]: item for item in ir["workflow"]["inputs"]}
        given = params.get("file_ids")
        files: dict[str, Any] = given if isinstance(given, dict) else {}
        missing = [name for name in _missing_inputs(ir) if name not in inputs and name not in files]
        if missing:
            labels = [
                _text((declared[name].get("schema") or {}).get("title") or name, 200)
                for name in missing[:5]
            ]
            raise Refused("invalid", f"Fill in {', '.join(labels)}.")
        if set(inputs) & set(_file_specs(ir)):
            raise Refused("invalid", "Send a file input's file with file_ids, not as a value.")
        try:
            # A file input's value is the path the desktop gives its upload later.
            _prepare_workflow_inputs(ir, {**inputs, **{name: "upload" for name in files}})
        except InvalidRattishWorkflowInputError as exc:
            raise Refused("invalid", self._clean(str(exc), registered, project, 600)) from None
        return dict(inputs)

    def _uploaded_files(
        self, peer: str, ir: dict[str, Any], file_ids: Any, base: Path
    ) -> dict[str, str]:
        """Save the files the phone uploaded for file inputs; the input values are paths.

        The phone uploads on its Rem file channel, as Share to Rem does. Each file is
        checked against its input's accept list and size, then written owner-only under
        the data folder's ``workflow-uploads``. The run copies it into its own folder and
        removes the upload; one nobody ran is swept after an hour (``file_inputs``).
        """
        from uuid import uuid4

        from gofer.devices.registry import PairingError
        from gofer.rattish.file_inputs import (
            FileInputError,
            _check_kind,
            _check_size,
            new_upload_path,
            remove_upload,
        )

        if not isinstance(file_ids, dict) or len(file_ids) > 1:
            raise Refused("invalid", "Send one file for the run.")
        specs = _file_specs(ir)
        files = self.control.app.files
        thread = self.control.files_thread(peer)
        saved: dict[str, str] = {}
        for name, identifier in file_ids.items():
            spec = specs.get(name)
            if spec is None:
                raise Refused("invalid", f"This workflow has no file input {_text(name, 200)}.")
            try:
                offer = files.metadata(peer, thread, _text(identifier, 64))
            except PairingError:
                raise Refused("invalid", UPLOAD_GONE) from None
            label = _text(offer.get("name"), 160) or "file"
            try:
                _check_size(spec, label, int(offer.get("size") or 0))
                _check_kind(spec, label, _text(offer.get("mime"), 200) or None)
            except FileInputError as exc:
                raise Refused("invalid", str(exc)) from None
            path = new_upload_path(base, uuid4().hex, label)
            try:
                files.save(peer, thread, _text(identifier, 64), path, limit=spec.max_size)
            except (PairingError, OSError):
                remove_upload(path, base)
                raise Refused("invalid", UPLOAD_GONE) from None
            # The desktop keeps its own copy now; the phone's upload isn't needed.
            files.cancel(peer, thread, _text(identifier, 64))
            saved[name] = str(path)
        return saved

    def _stop(
        self, registered: Any, project: str, params: dict[str, Any], base: Path
    ) -> dict[str, Any]:
        from gofer.ui.api import stop_workflow_run_payload

        run_id = _text(params.get("run_id"), 128)
        if not run_id:
            raise Refused("invalid", "Choose the run to stop.")
        result = stop_workflow_run_payload(registered.workflow_id, base, run_id=run_id)
        if result.get("stopped"):
            return self._answer(registered, run_id, "stopping", "Stopping this run.")
        return self._answer(
            registered,
            run_id,
            self._status(registered, run_id, base),
            self._clean(result.get("message") or NOT_RUNNING, registered, project, 300),
        )

    def _resume(
        self,
        peer: str,
        registered: Any,
        params: dict[str, Any],
        request: str,
        base: Path,
    ) -> dict[str, Any]:
        from gofer.ui.api import resume_workflow_payload

        prior = self.db.execute(
            "SELECT run FROM device_workflow_requests WHERE peer=? AND request=?",
            (peer, request),
        ).fetchone()
        if prior is not None:
            run_id = prior[0] or ""
            status = self._status(registered, run_id, base) if run_id else "queued"
            return self._answer(registered, run_id, status, "This run was already resumed.")
        earlier = _text(params.get("run_id"), 128)
        if not earlier:
            raise Refused("invalid", "Choose the run to resume.")
        # The desktop's own resume decides whether this run can be resumed. It
        # starts a new run in the background, as the desktop's Resume does.
        resumed = settle(
            lambda: resume_workflow_payload(
                registered.workflow_id,
                base,
                run_id=earlier,
                background=True,
                started_by="phone",
            )
        )
        run_id = _text((resumed or {}).get("runId"), 128) or None
        self.db.execute(
            "INSERT INTO device_workflow_requests VALUES (?,?,?,?)",
            (peer, request, registered.workflow_id, run_id),
        )
        if resumed is None:
            return self._answer(registered, "", "queued", STILL_STARTING)
        status = RUN_STATUS.get(str(resumed.get("status")), "unknown")
        return self._answer(
            registered, run_id or "", status, "Resumed on your computer as a new run."
        )

    def _rerun(
        self,
        peer: str,
        registered: Any,
        params: dict[str, Any],
        request: str,
        base: Path,
    ) -> dict[str, Any]:
        """Run again with the run's saved inputs, as the desktop's Run again does.

        The phone sends no inputs, so a run with withheld password inputs is
        refused by the desktop with the names it needs.
        """
        from gofer.ui.api import rerun_workflow_payload

        prior = self.db.execute(
            "SELECT run FROM device_workflow_requests WHERE peer=? AND request=?",
            (peer, request),
        ).fetchone()
        if prior is not None:
            run_id = prior[0] or ""
            status = self._status(registered, run_id, base) if run_id else "queued"
            return self._answer(registered, run_id, status, "This run was already started again.")
        earlier = _text(params.get("run_id"), 128)
        if not earlier:
            raise Refused("invalid", "Choose the run to run again.")
        started = settle(
            lambda: rerun_workflow_payload(
                earlier,
                base,
                workflow_id=registered.workflow_id,
                background=True,
                started_by="phone",
            )
        )
        run_id = _text((started or {}).get("runId"), 128) or None
        self.db.execute(
            "INSERT INTO device_workflow_requests VALUES (?,?,?,?)",
            (peer, request, registered.workflow_id, run_id),
        )
        if started is None:
            return self._answer(registered, "", "queued", STILL_STARTING)
        status = RUN_STATUS.get(str(started.get("status")), "unknown")
        return self._answer(
            registered,
            run_id or "",
            status,
            "Started again on your computer with the same inputs.",
        )


def settle(start: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    """Run a desktop workflow coroutine on its own thread and wait briefly for it.

    Returns None when it is still going after START_WAIT; it then finishes on
    its own and logs a failure, since nobody is waiting for the answer.
    """
    outcome: dict[str, Any] = {}
    done = threading.Event()
    abandoned = threading.Event()

    def target() -> None:
        try:
            outcome["value"] = asyncio.run(start())
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = exc
            if abandoned.is_set():
                logger.warning("Phone workflow request failed later: %s", type(exc).__name__)
        finally:
            done.set()

    threading.Thread(target=target, name="phone-workflow", daemon=True).start()
    if not done.wait(START_WAIT):
        abandoned.set()
        return None
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]
