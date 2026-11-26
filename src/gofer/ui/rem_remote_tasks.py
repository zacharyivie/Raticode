"""Rem tasks that run on a paired desktop, from both ends.

A research or conversation task the user pinned to a paired desktop (the task's
``computer``) is placed only there (gofer.ui.rem_placement). When every check passes
(gofer.core.placement_eligibility), Rem's tick calls ``hand_off``: this computer keeps
a stand-in run, "Running on <computer>", that holds no worker here. Its handle (the
computer, a request ID, the shared thread and the matched project) is saved on the run
before anything is sent.

``follow`` then sends the whole task once, as a job.submit with the desktop-only
``rem_task`` extension (gofer.devices.organization_jobs), and reads its status from
the device service's journal: the final answer, the report's path there, and what it
used. Stop sends request.cancel, and the stand-in settles as stopped when that desktop
reports the task cancelled. After a restart Rem's recovery leaves the stand-in alone and
``follow`` reconciles from the saved handle like organization fleet runs
(gofer.ui.organization_execution reconcile): it reads the status and never sends the
task again. While that desktop is offline the run waits for it instead of moving,
because its work is there. When it ends, this computer's usage ledger gets exactly one
summary row for it, with the real provider and model and the computer's ID, which
keeps it out of this computer's account totals.

On the receiving desktop, ``receive`` turns each handed-over task (accepted only on the
"Run threads from this desktop" grant, for read-only task types and at most
``REM_TASKS_PER_DESKTOP`` queued or running per desktop, by gofer.devices.application)
into a task for its own Rem in the matched project, pinned to itself so it is never
forwarded. Its run carries ``remote_origin``, naming the sending desktop, and its checks
name that desktop as their author. Rem admits it under its own worker limit and counts
it as ``rem``, after the user's own work here, as it does self-work. When the run ends
its answer, report path and usage go back; a Stop from the sender stops the run here
first.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from gofer.core.placement_engine import Placement
from gofer.devices.organization_jobs import (
    REM_TASK_BYTES,
    REM_TASK_CHECK,
    REM_TASK_CHECKS,
    REM_TASK_PROMPT,
)
from gofer.devices.registry import PairingError
from gofer.ui.rem_home_models import ACTIVE, RemConflict, desktop_author
from gofer.ui.rem_placement import start_for
from gofer.utils.logging import get_logger

if TYPE_CHECKING:
    from gofer.ui.rem_home import RemHome
    from gofer.ui.rem_placement import Snapshot

log = get_logger(__name__)

# How often a stand-in reads its task's status from the device journal.
FOLLOW_SECONDS = 1.0
# Device request states that end a handed-over task.
TERMINAL = {"completed", "failed", "cancelled", "rejected", "outcome_unknown"}
# Usage fields a desktop reports for a handed-over task.
USAGE_NUMBERS = ("cost_usd", "total_cost_usd", "total_tokens", "input_tokens", "output_tokens")
# The longest answer a stand-in keeps.
MAX_ANSWER = 256 * 1024


def offline_reason(name: str) -> str:
    return f"Waiting for {name} to come back; this run's work is there"


def answered_on(run: dict[str, Any]) -> dict[str, str] | None:
    """The paired desktop whose answer a run holds, or None for a run on this computer.

    A stand-in keeps its ``remote`` handle when it settles, so the handle marks every
    answer that came back from another desktop. Self-work never auto-approves what such
    an answer proposes (gofer.ui.rem_self_work ``_ingest``).
    """
    handle = run.get("remote")
    if not isinstance(handle, dict) or not handle.get("computer_id"):
        return None
    return {
        "id": str(handle["computer_id"]),
        "name": str(handle.get("computer_name") or "another computer"),
    }


class RemoteTasks:
    """Hands pinned tasks to paired desktops and runs the ones they hand here."""

    def __init__(self, home: RemHome) -> None:
        self.home = home
        # The device service (gofer.devices.control.DeviceControl); the UI server sets it.
        self.fleet: Any = None
        self.follow_seconds = FOLLOW_SECONDS
        self.next_follow = 0.0
        self.error: str | None = None

    def _application(self) -> Any:
        return getattr(self.fleet, "application", None) if self.fleet is not None else None

    def own_id(self) -> str | None:
        registry = getattr(self.fleet, "registry", None)
        identity = getattr(registry, "identity", None)
        value = getattr(identity, "device_id", None)
        return value if isinstance(value, str) else None

    def tick(self) -> None:
        """One pass, outside Rem's lock: the device service takes its own."""
        if self._application() is None:
            return
        try:
            self.receive()
            if time.monotonic() >= self.next_follow:
                self.next_follow = time.monotonic() + self.follow_seconds
                self.follow()
            if self.error is not None:
                log.info("Rem tasks on paired desktops are followed again")
            self.error = None
        except (ValueError, OSError, sqlite3.Error) as exc:
            if self.error is None:
                log.warning("Couldn't follow Rem tasks on paired desktops: %s", exc)
            self.error = str(exc) or type(exc).__name__

    # This computer: the stand-in ------------------------------------------------------

    def task_payload(self, run: dict[str, Any]) -> dict[str, Any]:
        """The ``rem_task`` a desktop needs to run this task as its own.

        Raises ValueError when the task is too long to send in one event.
        """
        recipe = run["recipe"]
        prompt = str(recipe["prompt"])
        if len(prompt) > REM_TASK_PROMPT:
            raise ValueError(
                f"Its prompt has {len(prompt):,} characters, and at most "
                f"{REM_TASK_PROMPT:,} can be sent to another computer"
            )
        criteria = recipe.get("acceptance_criteria") or []
        if len(criteria) > REM_TASK_CHECKS or any(
            len(str(c.get("description") or "")) > REM_TASK_CHECK for c in criteria
        ):
            raise ValueError(
                f"Another computer takes at most {REM_TASK_CHECKS} checks of up to "
                f"{REM_TASK_CHECK:,} characters each"
            )
        payload: dict[str, Any] = {
            "run_id": str(run["id"])[:100],
            "type": "research" if recipe["type"] == "research" else "errand",
            "execution_mode": recipe.get("execution_mode") or "work",
            "title": str(recipe["title"])[:160],
            "prompt": prompt,
            "checks": [
                {"id": str(c["id"])[:128], "description": str(c["description"])} for c in criteria
            ],
        }
        provider = start_for(run).provider
        if provider:
            payload["provider"] = provider
        size = len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())
        if size > REM_TASK_BYTES:
            raise ValueError(
                f"Its prompt and checks take {size // 1000} KB, and at most "
                f"{REM_TASK_BYTES // 1000} KB can be sent to another computer"
            )
        return payload

    def hand_off(self, run: dict[str, Any], snapshot: Snapshot, placed: Placement) -> None:
        """Turn a queued run placed on a paired desktop into its stand-in.

        Called in Rem's tick, under its lock, so it only writes the run: the handle is
        saved before ``follow`` sends anything. The run holds no worker here.
        """
        from gofer.devices.application import host_template
        from gofer.ui.rem_home import Superseded

        assert placed.computer_id is not None
        name = placed.computer_name or "the other computer"
        own = self.own_id()
        answer = snapshot.eligibility(placed.computer_id, start_for(run))
        try:
            if own is None or not answer.usable or not answer.project_id:
                self.home._update(
                    run["id"],
                    expected_epoch=run["epoch"],
                    waiting_reason=answer.reason or f"Waiting to reach {name}",
                )
                return
            try:
                self.task_payload(run)
            except ValueError as exc:
                self.home._finish(
                    run["id"], "failed", f"This task can't run on {name}: {exc}.", run["epoch"]
                )
                return
            record = placed.record(datetime.now(UTC).isoformat())
            record.update(chosen_by="user", remote=True)
            self.home._update(
                run["id"],
                expected_epoch=run["epoch"],
                state="executing",
                placement=record,
                remote={
                    "computer_id": placed.computer_id,
                    "computer_name": name,
                    "request_id": str(uuid4()),
                    "thread_id": host_template(own),
                    "project_id": answer.project_id,
                    "project_name": answer.project_name,
                    "provider": start_for(run).provider,
                    "sent": False,
                },
                phase_label=f"Running on {name}",
                waiting_reason=None,
            )
        except Superseded:
            return
        self.home.placement.claims.drop_wait(run["id"])

    def follow(self) -> None:
        """Send each saved hand-off once and follow every stand-in to its end."""
        with self.home.store.read() as db:
            runs = [
                json.loads(row[0])
                for row in db.execute(
                    f"SELECT body FROM runs WHERE state IN ({','.join('?' * len(ACTIVE))})",
                    sorted(ACTIVE),
                )
            ]
        stand_ins = [r for r in runs if r.get("remote")]
        if stand_ins:
            # Keeps the computers' reading fresh, so an offline desktop shows as one.
            self.home.placement.ask()
        for run in stand_ins:
            try:
                self.follow_run(run)
            except PairingError as exc:
                handle = run["remote"]
                name = handle["computer_name"]
                if self._paired(handle["computer_id"]):
                    waiting = (
                        f"Waiting to reach {name} ({exc})"
                        if self._online(handle["computer_id"])
                        else offline_reason(name)
                    )
                    self._show(run, waiting_reason=waiting)
                elif run["state"] == "cancelling":
                    self.home._finish(
                        run["id"],
                        "cancelled",
                        f"Stopped here. {name} is no longer paired with this computer, so "
                        "it may still be running there.",
                    )
                else:
                    self._show(
                        run,
                        waiting_reason=f"{name} is no longer paired with this computer; "
                        "stop this run, or pair it again",
                    )

    def _online(self, computer_id: str) -> bool:
        """Whether the latest reading has that desktop online; unknown counts as online."""
        view = self.home.placement.view
        if view is None:
            return True
        for computer in view.get("computers") or []:
            if computer.get("id") == computer_id:
                return bool(computer.get("state") == "online")
        return False

    def _paired(self, computer_id: str) -> bool:
        registry = getattr(self.fleet, "registry", None)
        if registry is None:
            return False
        try:
            with registry.lock:
                return bool(registry._row(computer_id)["state"] == "active")
        except PairingError:
            return False

    def _status(self, handle: dict[str, Any]) -> dict[str, Any]:
        return dict(
            self.fleet.action(
                {
                    "action": "work_status",
                    "device_id": handle["computer_id"],
                    "request_id": handle["request_id"],
                }
            )
        )

    def _send(self, run: dict[str, Any], handle: dict[str, Any]) -> None:
        self.fleet.action(
            {
                "action": "send",
                "kind": "job.submit",
                "device_id": handle["computer_id"],
                "thread_id": handle["thread_id"],
                "project_id": handle["project_id"],
                "request_id": handle["request_id"],
                "text": str(run["recipe"]["title"])[:4000],
                "rem_task": self.task_payload(run),
            }
        )

    def _show(self, run: dict[str, Any], **patch: Any) -> None:
        """Change what a stand-in shows, only when it changed."""
        from gofer.ui.rem_home import Superseded

        if all(run.get(key) == value for key, value in patch.items()):
            return
        try:
            self.home._update(run["id"], **patch)
        except Superseded:
            pass

    def follow_run(self, run: dict[str, Any]) -> None:
        handle = dict(run["remote"])
        name = handle["computer_name"]
        if not handle.get("sent"):
            if run["state"] == "cancelling":
                # Nothing reached that desktop, so the run stops here.
                self.home._settle_stop(run["id"])
                return
            # A handle with no request in the journal was never queued, so this is its
            # first and only send. After a restart a queued request is never sent again.
            if self._status(handle)["state"] == "unknown":
                try:
                    self._send(run, handle)
                except ValueError as exc:
                    if not isinstance(exc, PairingError):
                        self.home._finish(
                            run["id"], "failed", f"This task can't run on {name}: {exc}."
                        )
                        return
                    raise
            handle["sent"] = True
            self._show(run, remote=handle)
            return
        status = self._status(handle)
        events = status.get("events") or []
        updates = [e["payload"] for e in events if e.get("type") == "job.status"]
        state = status.get("state")
        if state in TERMINAL:
            final: dict[str, Any] = next(
                (u for u in reversed(updates) if u["state"] in TERMINAL), {}
            )
            text = "".join(
                e["payload"].get("text", "") for e in events if e.get("type") == "chat.message"
            )[:MAX_ANSWER]
            self.settle(run, handle, str(state), final, text)
            return
        if run["state"] == "cancelling" and not handle.get("cancel_sent"):
            try:
                self.fleet.action(
                    {
                        "action": "cancel_work",
                        "device_id": handle["computer_id"],
                        "request_id": handle["request_id"],
                    }
                )
            except PairingError as exc:
                if self._paired(handle["computer_id"]):
                    raise  # Shown as waiting; Stop is sent again on the next pass.
                # That desktop is no longer paired: nothing can stop it there now.
                self.home._finish(
                    run["id"],
                    "cancelled",
                    f"Stopped here. {name} couldn't be told to stop it ({exc}), so it may "
                    "still be running there.",
                )
                return
            handle["cancel_sent"] = True
            self._show(run, remote=handle, phase_label=f"Stopping on {name}")
            return
        detail = str(updates[-1].get("detail") or "") if updates else ""
        if not self._online(handle["computer_id"]):
            waiting: str | None = offline_reason(name)
        elif detail.startswith("Waiting"):
            waiting = detail
        else:
            waiting = None
        label = f"Stopping on {name}" if run["state"] == "cancelling" else f"Running on {name}"
        self._show(run, waiting_reason=waiting, phase_label=label)

    def settle(
        self,
        run: dict[str, Any],
        handle: dict[str, Any],
        state: str,
        final: dict[str, Any],
        text: str,
    ) -> None:
        """End a stand-in with what its desktop reported, and record its usage once."""
        name = handle["computer_name"]
        self.record_usage(run, handle, state, final.get("usage") or {})
        report = (final.get("rem_task") or {}).get("report")
        detail = str(final.get("detail") or "")
        handle["result"] = {
            "state": state,
            "run_id": (final.get("rem_task") or {}).get("run_id"),
            "report": report,
        }
        try:
            self.home._update(run["id"], remote=handle, worker_result=text or None)
        except Exception:  # noqa: BLE001 - the outcome below still settles the run.
            log.warning("Couldn't save the result of a task run on %s", name, exc_info=True)
        if state == "completed":
            summary = text.strip() or f"{name} finished this task."
            if report:
                summary += f"\n\nReport saved on {name}: {report}"
            self.home._finish(run["id"], "succeeded", summary)
        elif state == "cancelled":
            if run["state"] == "cancelling":
                self.home._settle_stop(run["id"])
            else:
                self.home._finish(run["id"], "cancelled", f"Stopped on {name}.")
        elif state == "rejected":
            self.home._finish(run["id"], "failed", f"{name} refused this task: {detail}")
        else:
            reason = text.strip() or detail or "it reported no reason"
            self.home._finish(run["id"], "failed", f"{name} couldn't finish this task: {reason}")

    def record_usage(
        self, run: dict[str, Any], handle: dict[str, Any], state: str, usage: dict[str, Any]
    ) -> None:
        """This computer's one summary row for a run on a paired desktop.

        Tagged with that computer and the provider and model that ran it there; the
        same invocation ID each time, so a repeat replaces it instead of adding one.
        """
        from gofer.core.usage_ledger import record_invocation

        provider = usage.get("provider") or handle.get("provider") or "unknown"
        record_invocation(
            invocation_id=f"remote-run:{run['id']}",
            provider=str(provider),
            model=str(usage["model"]) if usage.get("model") else None,
            metadata={k: usage[k] for k in USAGE_NUMBERS if usage.get(k) is not None},
            status="completed" if state == "completed" else state,
            data_dir=self.home.data_dir,
            run_id=run["id"],
            computer_id=handle["computer_id"],
        )

    # The receiving desktop -------------------------------------------------------------

    def receive(self) -> None:
        """Start, stop and report the Rem tasks paired desktops handed to this one."""
        app = self._application()
        for item in app.rem_tasks():
            try:
                self.receive_one(app, item)
            except PairingError as exc:
                log.info("A handed-over Rem task's result wasn't delivered: %s", exc)

    def receive_one(self, app: Any, item: dict[str, Any]) -> None:
        peer, request = item["peer"], item["request"]
        if item["run_id"] is None:
            if item["state"] == "cancelled" or not item["authorized"]:
                app.finish_rem_task(
                    peer, request, "cancelled", detail=f"Stopped before it started on {app.name}"
                )
                return
            try:
                run_id = self.accept(item)
            except (ValueError, RemConflict) as exc:
                app.finish_rem_task(
                    peer, request, "rejected", detail=f"{app.name} couldn't start it: {exc}"
                )
                return
            app.rem_task_started(peer, request, run_id)
            return
        with self.home.store.read() as db:
            row = db.execute("SELECT body FROM runs WHERE id=?", (item["run_id"],)).fetchone()
        if row is None:
            app.finish_rem_task(
                peer, request, "failed", detail=f"The task's run is gone on {app.name}"
            )
            return
        run = json.loads(row[0])
        if run["state"] in ACTIVE or run["state"] == "paused":
            stop = item["state"] == "cancelled" or not item["authorized"]
            if stop and run["state"] != "cancelling":
                # The sender pressed Stop, or no longer may run work here.
                self.home.store.mutate_run(
                    run["id"], {"request_id": f"remote-stop:{request}"}, "cancel"
                )
            elif run["state"] == "queued" and run.get("waiting_reason"):
                app.rem_task_waiting(peer, request, str(run["waiting_reason"]))
            return
        from gofer.core.usage_ledger import run_summary

        state = {"succeeded": "completed", "cancelled": "cancelled"}.get(run["state"], "failed")
        summary = str(run.get("summary_short") or "")
        text = str(run.get("worker_result") or summary) if state == "completed" else summary
        app.finish_rem_task(
            peer,
            request,
            state,
            text[:MAX_ANSWER],
            usage=run_summary(run["id"], self.home.data_dir),
            run_id=run["id"],
            report=self.report(run, item.get("root")),
        )

    def accept(self, item: dict[str, Any]) -> str:
        """Create this desktop's own Rem task for a handed-over one; returns its run ID.

        Idempotent per request, so a restart between creating it and recording it
        makes no second task. It is pinned to this computer, so it is never forwarded.
        """
        task = item["task"]
        if not item.get("root"):
            raise ValueError("that project is no longer shared")
        recipe: dict[str, Any] = {
            "title": task["title"],
            "type": task["type"],
            "prompt": task["prompt"],
            "project_dir": item["root"],
            "execution_mode": task["execution_mode"],
            "computer": self.home.computer_id(),
            "schedule": {"kind": "once", "timezone": "UTC"},
        }
        if task.get("checks"):
            # The sending desktop wrote these, not the user on this computer.
            author = desktop_author(item["peer"])
            recipe["acceptance_criteria"] = [
                {"id": c["id"], "description": c["description"], "authored_by": author}
                for c in task["checks"]
            ]
        if task.get("provider"):
            recipe["provider"] = task["provider"]
        created = self.home.create_task(
            {"request_id": f"remote:{item['request']}", **recipe},
            # Saved with the run: it waits behind this computer's own work for a worker.
            remote_origin={
                "computer_id": item["peer"],
                "computer_name": item["peer_name"],
                "request_id": item["request"],
                "run_id": task["run_id"],
            },
        )
        run = created["task"].get("active_run") or created["task"].get("last_run")
        if not run:
            raise ValueError("Rem didn't start it")
        return str(run["id"])

    @staticmethod
    def report(run: dict[str, Any], root: str | None) -> str | None:
        """The path of the report a run saved, relative to its project; else its name."""
        saved = [
            a["path"]
            for a in run.get("artifacts") or []
            if a.get("path") and not a.get("superseded") and a.get("epoch") == run.get("epoch")
        ]
        if not saved:
            return None
        path = Path(saved[-1])
        if root:
            try:
                return str(path.resolve().relative_to(Path(root).resolve()))[:500]
            except (ValueError, OSError):
                pass
        return path.name[:500]
