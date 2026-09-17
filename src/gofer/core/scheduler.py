from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from apscheduler.jobstores.base import JobLookupError
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import SchedulerNotRunningError
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.pool import NullPool

from gofer.core.run_outputs import write_run_node_outputs_payload
from gofer.core.workflow import AgenticWorkflow
from gofer.utils.logging import get_logger
from gofer.utils.run_state import workflow_stop_path

log = get_logger(__name__)


def _run_workflow(workflow_id: str, workflow_path: str, subscriptions: dict[str, Any]) -> None:
    path = Path(workflow_path)
    wf = AgenticWorkflow.from_file(path)
    wf.validate(path)
    for warning in wf.resource_warnings(path.parent):
        log.warning("%s", warning)
    from gofer.core.executor import WorkflowExecutor
    from gofer.subscriptions.acp_providers import AcpSubscription
    from gofer.subscriptions.antigravity import AntigravitySubscription
    from gofer.subscriptions.claude_code import ClaudeCodeSubscription
    from gofer.subscriptions.cli_providers import CliSubscription
    from gofer.subscriptions.codex import CodexSubscription

    runtime_subscriptions = subscriptions or {
        "claude_code": ClaudeCodeSubscription(),
        "codex": CodexSubscription(),
        **{provider: CliSubscription(provider) for provider in ("cursor", "copilot", "opencode")},
        "antigravity": AntigravitySubscription(),
        "grok": AcpSubscription("grok"),
    }

    async def _exec() -> None:
        executor = WorkflowExecutor(
            wf,
            runtime_subscriptions,
            log_base_dir=path.parent / "logs",
            workflow_path=path,
            stop_file=workflow_stop_path(workflow_id, path.parent),
        ).with_parameters(wf.config.schedule.invocation_inputs if wf.config.schedule else {})
        result = await executor.run()
        write_run_node_outputs_payload(result, wf.config.resource_limits)
        log.info("Workflow %s finished: success=%s", workflow_id, result.success)

    asyncio.run(_exec())


class WorkflowScheduler:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path
        self._persistent = db_path is not None
        self._jobstore = self._new_jobstore()
        self._scheduler = BackgroundScheduler(jobstores={"default": self._jobstore})
        self._workflow_paths: dict[str, str] = {}
        self._closed = False

    def _new_jobstore(self) -> SQLAlchemyJobStore | MemoryJobStore:
        if self._db_path:
            db_url = f"sqlite:///{self._db_path}"
            engine_options: dict[str, Any] = {"poolclass": NullPool}
            return SQLAlchemyJobStore(
                url=db_url,
                engine_options=engine_options,
            )
        return MemoryJobStore()

    def _new_scheduler(self) -> BackgroundScheduler:
        return BackgroundScheduler(jobstores={"default": self._jobstore})

    def add_workflow(self, workflow: AgenticWorkflow, workflow_path: Path) -> None:
        if workflow.config.schedule is None:
            raise ValueError(f"Workflow '{workflow.config.id}' has no schedule configured")
        if not workflow_path.exists():
            raise FileNotFoundError(f"Workflow file not found: {workflow_path}")
        workflow.validate(workflow_path)

        sched = workflow.config.schedule
        trigger = CronTrigger.from_crontab(sched.cron_expression, timezone=sched.timezone)
        job_id = f"workflow:{workflow.config.id}"
        self._workflow_paths[workflow.config.id] = str(workflow_path)

        # Start transiently so jobs persist to the SQLAlchemy store immediately
        started_here = False
        if self._persistent and not self._scheduler.running:
            self._scheduler.start(paused=True)
            started_here = True

        # Remove first so replace works regardless of scheduler running state
        try:
            self._scheduler.remove_job(job_id)
        except JobLookupError:
            pass

        self._scheduler.add_job(
            _run_workflow,
            trigger=trigger,
            id=job_id,
            name=workflow.config.name,
            args=[workflow.config.id, str(workflow_path), {}],
            coalesce=True,
            max_instances=1,
        )

        if started_here:
            self._shutdown_scheduler(wait=False)

        log.info("Scheduled workflow '%s' cron='%s'", workflow.config.id, sched.cron_expression)

    def remove_workflow(self, workflow_id: str) -> None:
        job_id = f"workflow:{workflow_id}"
        started_here = False
        if self._persistent and not self._scheduler.running:
            self._scheduler.start(paused=True)
            started_here = True
        try:
            self._scheduler.remove_job(job_id)
        except JobLookupError:
            pass
        finally:
            if started_here:
                self._shutdown_scheduler(wait=False)

    def list_workflows(self) -> list[dict[str, str]]:
        started_here = False
        if self._persistent and not self._scheduler.running:
            self._scheduler.start(paused=True)
            started_here = True
        try:
            jobs_raw = list(self._scheduler.get_jobs())
        finally:
            if started_here:
                self._shutdown_scheduler(wait=False)
        jobs = []
        for job in jobs_raw:
            jobs.append(
                {
                    "id": job.id.removeprefix("workflow:"),
                    "name": job.name,
                    "next_run": str(job.next_run_time)
                    if getattr(job, "next_run_time", None)
                    else "paused",
                }
            )
        return jobs

    def start(self, paused: bool = False) -> None:
        self._closed = False
        self._scheduler.start(paused=paused)

    def resume(self) -> None:
        self._scheduler.resume()

    def shutdown(self, wait: bool = True) -> None:
        self._shutdown_scheduler(wait=wait)
        try:
            self._jobstore.shutdown()
        except Exception:
            pass
        self._closed = True

    def _shutdown_scheduler(self, wait: bool = True) -> None:
        try:
            self._scheduler.shutdown(wait=wait)
        except SchedulerNotRunningError:
            self._dispose_jobstore()
        else:
            if self._persistent:
                try:
                    self._jobstore.shutdown()
                except Exception:
                    pass
                self._jobstore = self._new_jobstore()
                self._closed = False
            else:
                self._closed = True
            self._scheduler = self._new_scheduler()

    def is_running(self) -> bool:
        return bool(self._scheduler.running)

    def _dispose_jobstore(self) -> None:
        if self._closed:
            return
        self._jobstore.shutdown()
        self._closed = True

    def __del__(self) -> None:
        try:
            if self._scheduler.running:
                self._shutdown_scheduler(wait=False)
            else:
                self._dispose_jobstore()
        except Exception:
            pass
