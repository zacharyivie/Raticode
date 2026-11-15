"""Workflows on a paired phone: the list, one workflow's graph and runs, and controls.

The phone sees Rattish workflows in desktop projects its catalog names, the same
scope as swarms. Run, stop and resume call the functions the desktop window
calls, so every desktop check still applies: the run must use the saved source
revision the phone was shown, active-run limits and approval gates hold, and a
resume follows the desktop's resume rules (``resume_rattish_run``) and starts a
new run. The phone can't edit a workflow.

A run can take typed inputs. ``workflows.read`` lists the workflow's inputs as form
fields built from the same input schema the desktop's Run dialog reads, and
``workflows.run`` sends the values back in ``inputs``. The desktop checks them
against that schema before anything starts, and the phone guard has already
screened them like any other request text. A phone that can show the form says so
with ``input_form`` on its read; older phones are still told to start such a
workflow on the desktop. Inputs whose schema says ``format: password`` are masked
on the phone and their defaults stay here, as do workflow secrets.

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

Results carry no source, run input values, node outputs or provider configuration.
Free text such as errors has the project and data folders replaced by names.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gofer.devices.desktop_control import Refused

logger = logging.getLogger(__name__)

READS = frozenset({"workflows.list", "workflows.read", "workflows.runs"})
ACTIONS = frozenset(
    {
        "workflows.run",
        "workflows.stop",
        "workflows.resume",
        "workflows.rerun",
        "workflows.trigger",
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
STILL_STARTING = "Your computer is still starting this run. It will appear in the run list."
BAD_INPUTS = "Send the run's inputs as named values."
TOO_LARGE = "These inputs are too large to send from the phone. Start this run on your computer."
# Triggers shown on one workflow's page.
TRIGGER_LIMIT = 50
CONFIRM_TRIGGER = "Confirm on your phone before turning this trigger on."
CONFIRM_RESUME = "Confirm on your phone before resuming all triggers."
NO_TRIGGER = "This trigger no longer exists on your computer."
TRIGGER_KINDS = {"cron": "Schedule", "file": "File change", "webhook": "Webhook"}
RUN_STATUS = {
    "running": "running",
    "success": "succeeded",
    "error": "failed",
    "stopped": "stopped",
    "disconnected": "interrupted",
}
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


def _missing_inputs(ir: dict[str, Any]) -> list[str]:
    """Inputs a run needs that have no default. The phone can't supply them."""
    return [
        str(item["name"])
        for item in ir["workflow"]["inputs"]
        if item["required"] and not item["default"]["present"]
    ]


def _input_kind(schema: dict[str, Any]) -> str:
    """The phone field for an input: the same split the desktop's Run dialog makes."""
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
        "runs": runs,
        "plan": plan,
        "detail": detail,
        "resume": resume,
        "rerun": rerun,
        "triggers": _gather_triggers(workflow.entrypoint, base),
    }


def _gather_pause(base: Path) -> dict[str, Any]:
    from gofer.rattish.triggers import TriggerStore

    store = TriggerStore(base)
    triggers = store.list_triggers()
    return {
        "pause": store.pause_state(),
        "count": len(triggers),
        "enabled": sum(1 for item in triggers if item.enabled),
    }


def _gather_triggers(entrypoint: Path, base: Path) -> dict[str, Any]:
    """One workflow's triggers with their last results, and whether all are paused."""
    from gofer.rattish.triggers import TriggerStore, next_run_at

    store = TriggerStore(base)
    items = []
    for record in store.list_triggers(entrypoint)[:TRIGGER_LIMIT]:
        items.append(
            {
                "record": record,
                "next_run_at": next_run_at(record),
                "latest": store.latest_event(record.id),
            }
        )
    return {"items": items, "pause": store.pause_state()}


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
        text = _text(value, 4000)
        base = self.base
        for folder, label in (
            (str(registered.project_root), project),
            (str(base) if base else "", "Raticode data"),
        ):
            if folder:
                text = text.replace(folder, label)
        return text[:limit]

    # Reads -------------------------------------------------------------------

    def read(self, peer: str, op: str, params: dict[str, Any], data: dict[str, Any]) -> Any:
        if op == "workflows.runs":
            return self._all_runs(peer, data)
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
        runs = [self._run(run) for run in data["runs"]["runs"]]
        waiting = data["runs"]["awaitingNodes"]
        plan = data["plan"]
        shown = None
        if detail is not None:
            shown = self._detail(detail, waiting, registered, project, nodes)
            # The detail was read last. A run that ended in between must not
            # show as both running and finished in one answer.
            fresh = {key: value for key, value in shown.items() if key not in {"error", "nodes"}}
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
            ),
        }

    def _all_runs(self, peer: str, data: dict[str, Any]) -> dict[str, Any]:
        """Every open project's workflow runs, newest first, with totals for the filter."""
        projects = self._open_projects(peer)
        runs = []
        for run in data["runs"]:
            project = projects.get(_same(run["projectRoot"]))
            if project is None:
                continue  # Closed on the desktop after the read.
            usage = run.get("usage") or {}
            shown = self._run(run)
            if run.get("fallbacks"):
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
        }

    def _trigger(self, item: dict[str, Any], registered: Any, project: str) -> dict[str, Any]:
        """A trigger as the phone lists it. A webhook's address and token stay here."""
        record = item["record"]
        config = record.config
        if record.kind == "cron":
            description = f"{config.get('expression')} ({config.get('timezone')})"
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
            "next_run_at": _text(item["next_run_at"], 80),
            "last_result": None
            if latest is None
            else {
                "status": _text(latest.status, 20),
                "at": _text(latest.at, 80),
                "message": self._clean(latest.message, registered, project, 300),
            },
        }

    @staticmethod
    def _recency(item: dict[str, Any]) -> str:
        latest = item.get("latest_run") or {}
        return max(str(latest.get("started_at") or ""), str(item.get("updated_at") or ""))

    def _summary(self, item: dict[str, Any], project: str) -> dict[str, Any]:
        registered = item["registered"]
        ir = item["ir"]
        runs = item["runs"]["runs"]
        latest = self._run(runs[0]) if runs else None
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
        }

    @staticmethod
    def _run(run: dict[str, Any]) -> dict[str, Any]:
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
        error = detail.get("error") or {}
        return {
            **run,
            "error": self._clean(
                error.get("message") if isinstance(error, dict) else error,
                registered,
                project,
                600,
            ),
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
    ) -> dict[str, Any]:
        """``form`` is True for a phone that shows the Run form and can supply inputs.

        The phone never sends inputs with Resume or Run again, so a run whose password
        inputs weren't saved names them and says to use the computer instead.
        """

        def control(reason: str) -> dict[str, Any]:
            return {"allowed": not reason, "reason": reason}

        if ir is None:
            run = INVALID
        elif plan is None:
            run = "Your computer couldn't prepare this workflow. Open it there to check it."
        elif plan.get("requiredSecrets"):
            run = _missing_secrets_reason(plan["requiredSecrets"])
        elif plan.get("blockingDiagnostics"):
            run = "This workflow can't run yet. Open it on your computer to see why."
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
            if op == "workflows.run":
                return self._start(peer, registered, project, params, request, base)
            if op == "workflows.stop":
                return self._stop(registered, project, params, base)
            if op == "workflows.rerun":
                return self._rerun(peer, registered, params, request, base)
            if op == "workflows.trigger":
                return self._set_trigger(registered, params, base)
            return self._resume(peer, registered, params, request, base)
        except WorkflowRunError as exc:
            message = str(exc)
            if "source changed" in message:
                raise Refused("conflict", CHANGED) from None
            raise Refused("invalid", self._clean(message, registered, project, 1000)) from None

    def _set_trigger(self, registered: Any, params: dict[str, Any], base: Path) -> dict[str, Any]:
        """Turn one of this workflow's triggers on or off. On needs the phone's confirmation."""
        from gofer.rattish.triggers import TriggerError, TriggerNotFound, TriggerStore

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
            record = store.update(trigger_id, enabled=enabled)
        except TriggerNotFound:
            raise Refused("not_found", NO_TRIGGER) from None
        except TriggerError as exc:
            raise Refused("invalid", _text(exc, 600)) from None
        if not enabled:
            message = "Turned off. Its settings and history are kept."
        elif store.pause_state().paused:
            message = "Turned on. All triggers are paused, so it starts when you resume them."
        else:
            message = "Turned on."
        logger.info("Phone turned trigger %s %s", record.id, "on" if enabled else "off")
        return {
            "workflow_id": registered.workflow_id,
            "trigger_id": record.id,
            "enabled": record.enabled,
            "message": message,
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
            if _missing_inputs(ir):
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
        missing = [name for name in _missing_inputs(ir) if name not in inputs]
        if missing:
            labels = [
                _text((declared[name].get("schema") or {}).get("title") or name, 200)
                for name in missing[:5]
            ]
            raise Refused("invalid", f"Fill in {', '.join(labels)}.")
        try:
            _prepare_workflow_inputs(ir, inputs)
        except InvalidRattishWorkflowInputError as exc:
            raise Refused("invalid", self._clean(str(exc), registered, project, 600)) from None
        return dict(inputs)

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
