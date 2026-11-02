"""Workflows on a paired phone: the list, one workflow's graph and runs, and controls.

The phone sees Rattish workflows in desktop projects its catalog names, the same
scope as swarms. Run, stop and resume call the functions the desktop window
calls, so every desktop check still applies: the run must use the saved source
revision the phone was shown, active-run limits and approval gates hold, and a
resume follows the desktop's resume rules (``resume_rattish_run``) and starts a
new run. The phone can't edit a workflow or supply
run inputs, so a workflow that needs inputs without defaults is started from
the desktop.

Results carry no source, inputs, node outputs or provider configuration. Free
text such as errors has the project and data folders replaced by names.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gofer.devices.desktop_control import Refused

logger = logging.getLogger(__name__)

READS = frozenset({"workflows.list", "workflows.read"})
ACTIONS = frozenset({"workflows.run", "workflows.stop", "workflows.resume"})
# The phone's request is answered on the device loop under the device lock. A
# run start normally acknowledges well within this; a slower one keeps going
# and appears in the run list.
START_WAIT = 5.0
LIST_LIMIT = 100
RUN_LIMIT = 20
NODE_LIMIT = 200
EDGE_LIMIT = 400
NOT_OPEN = "This workflow is not in an open desktop project."
NEEDS_INPUTS = "This workflow needs inputs. Start it on your computer."
CHANGED = "This workflow changed on your computer. Review it again before running."
REFRESH = "Refresh this workflow, then run it again."
INVALID = "This workflow has errors. Open it on your computer to fix them."
NOT_RUNNING = "No run is going right now."
NO_RUN = "There is no run to resume."
NO_RESUME = "Your computer can't resume this run."
STILL_STARTING = "Your computer is still starting this run. It will appear in the run list."
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


def _modified(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
    except OSError:
        return ""


def gather(op: str, params: dict[str, Any], base: Path | None) -> dict[str, Any]:
    """Read workflows for a phone view. Runs outside the device lock."""
    from gofer.rattish.workspaces import list_registered_workflows
    from gofer.ui.api import (
        WorkflowLogError,
        WorkflowPlanError,
        rattish_resume_state_payload,
        recent_rattish_runs_payload,
        workflow_plan_payload,
        workflow_run_log_payload,
    )

    if base is None:
        raise Refused("unavailable", "Workflows are not available on this desktop.")
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
        return {"workflows": items}
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
    return {
        "registered": workflow,
        "ir": ir,
        "runs": runs,
        "plan": plan,
        "detail": detail,
        "resume": resume,
    }


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
        if op == "workflows.list":
            projects = self._open_projects(peer)
            items = []
            for item in data["workflows"]:
                project = projects.get(_same(item["registered"].project_root))
                if project is not None:
                    items.append(self._summary(item, project))
            items.sort(key=self._recency, reverse=True)
            return {"workflows": items[:LIST_LIMIT], "earlier": max(0, len(items) - LIST_LIMIT)}
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
            "controls": self._controls(data["ir"], plan, runs, data.get("resume")),
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
        return {
            "workflow_id": _text(registered.workflow_id, 200),
            "name": _text((ir or {}).get("workflow", {}).get("name") or registered.name, 200),
            "description": description,
            "project_name": project,
            "status": status,
            "updated_at": _modified(registered.entrypoint),
            "latest_run": latest,
        }

    @staticmethod
    def _run(run: dict[str, Any]) -> dict[str, Any]:
        status = RUN_STATUS.get(str(run.get("status")), _text(run.get("status"), 40))
        if run.get("awaitingApproval") and status == "running":
            status = "waiting"
        seconds = float(run.get("durationSeconds") or 0)
        if status == "waiting":
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
                "startedAt": document.get("started_at"),
                "finishedAt": None
                if document.get("status") == "running"
                else document.get("finished_at"),
                "durationSeconds": float(document.get("duration_ms") or 0) / 1000,
                "triggerType": "manual",
                "resumedFrom": detail.get("resumedFrom"),
            }
        )
        active = run["status"] in {"running", "waiting"}
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
                    record.get("message")
                    or ("Reused from the earlier run" if record.get("reused") else "")
                    or record.get("activationSummary")
                    or ("Failed, allowed to continue" if raw == "allowed_failure" else ""),
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
    ) -> dict[str, Any]:
        def control(reason: str) -> dict[str, Any]:
            return {"allowed": not reason, "reason": reason}

        if ir is None:
            run = INVALID
        elif plan is None:
            run = "Your computer couldn't prepare this workflow. Open it there to check it."
        elif plan.get("blockingDiagnostics"):
            run = "This workflow can't run yet. Open it on your computer to see why."
        elif not plan.get("runnable"):
            run = "This workflow has no nodes to run."
        elif _missing_inputs(ir):
            run = NEEDS_INPUTS
        else:
            run = ""
        active = any(r["status"] in {"running", "waiting"} for r in runs)
        if resume is None:
            resumable = control(NO_RUN)
        else:
            resumable = control("" if resume["allowed"] else str(resume["reason"]) or NO_RESUME)
            # Steps a resume runs again, for the phone's confirmation.
            resumable["rerun_nodes"] = [
                _text(node, 200) for node in resume.get("rerunNodes") or []
            ][:NODE_LIMIT]
        return {
            "run": control(run),
            "stop": control("" if active else NOT_RUNNING),
            # About the shown run, which is the one the phone's Resume button names.
            "resume": resumable,
        }

    # Actions -----------------------------------------------------------------

    def act(self, peer: str, op: str, params: dict[str, Any], request: str) -> dict[str, Any]:
        from gofer.rattish.workspaces import list_registered_workflows
        from gofer.ui.api import WorkflowRunError

        base = self.base
        if base is None:
            raise Refused("unavailable", "Workflows are not available on this desktop.")
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
            return self._resume(peer, registered, params, request, base)
        except WorkflowRunError as exc:
            message = str(exc)
            if "source changed" in message:
                raise Refused("conflict", CHANGED) from None
            raise Refused("invalid", self._clean(message, registered, project, 1000)) from None

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
        if _missing_inputs(ir):
            raise Refused("invalid", NEEDS_INPUTS)
        # The same background start the desktop's Run button uses, with its
        # saved-revision check, active-run limit and preflight.
        started = settle(
            lambda: run_workflow_payload(
                registered.workflow_id,
                base,
                background=True,
                expected_revision=revision,
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
                registered.workflow_id, base, run_id=earlier, background=True
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
