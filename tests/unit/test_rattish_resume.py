"""Resuming Rattish runs: replayed results, rerun work and refused runs."""

from __future__ import annotations

import json
import os
import threading
from collections import Counter
from pathlib import Path
from typing import Any

import anyio
import pytest

from gofer.rattish.run_service import (
    RattishResumeRefused,
    RattishRunResult,
    effective_run_status,
    resume_rattish_run,
    resume_refusal,
    resume_rerun_nodes,
    run_rattish_file,
)
from gofer.rattish.runtime import DEFAULT_NODE_HANDLERS, HandlerResult, NodeHandlerRegistry
from gofer.rattish.workspaces import RegisteredWorkflow, create_registered_workflow

LINEAR = """Rattish: 1
Workflow:
  name: Linear
Node build:
  type: bash-command
  command: build
  to: test
Node test:
  type: bash-command
  command: test
  needs: build
  to: ship
Node ship:
  type: bash-command
  command: ship
  needs: test
"""


def done(stdout: str = "") -> dict[str, Any]:
    return {"stdout": stdout, "stderr": "", "exit_code": 0}


class Fake:
    """A bash-command stand-in that counts calls and fails nodes on demand."""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()
        self.fail: Counter[str] = Counter()
        self.block: set[str] = set()
        self.started = threading.Event()
        self.release = threading.Event()

    async def __call__(self, node, context, bindings) -> HandlerResult:
        node_id = node["id"]
        self.calls[node_id] += 1
        if node_id in self.block:
            self.started.set()
            while not self.release.is_set():
                await anyio.sleep(0.01)
        if self.fail[node_id]:
            self.fail[node_id] -= 1
            result = done()
            result["exit_code"] = 1
            return HandlerResult(False, result)
        return HandlerResult(True, done(f"{node_id}-{self.calls[node_id]}"))

    def handlers(self) -> NodeHandlerRegistry:
        return NodeHandlerRegistry(
            {**DEFAULT_NODE_HANDLERS._handlers, "raticode.bash_command": self}
        )


def workflow(tmp_path: Path, source: str = LINEAR) -> tuple[Path, RegisteredWorkflow]:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    base = tmp_path / "data"
    registered = create_registered_workflow(project, "Resume check", registry_dir=base)
    registered.entrypoint.write_text(source, encoding="utf-8")
    return base, registered


async def run(
    base: Path, registered: RegisteredWorkflow, fake: Fake, **options: Any
) -> RattishRunResult:
    return await run_rattish_file(
        registered.entrypoint, data_dir=base, handlers=fake.handlers(), **options
    )


async def resume(
    base: Path,
    registered: RegisteredWorkflow,
    fake: Fake,
    document: Any,
    **options: Any,
) -> RattishRunResult:
    return await resume_rattish_run(
        registered.entrypoint, document, data_dir=base, handlers=fake.handlers(), **options
    )


@pytest.mark.anyio
async def test_a_failed_run_resumes_from_the_failed_node(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    fake = Fake()
    fake.fail["test"] = 1
    first = await run(base, registered, fake)
    assert first.status == "failed"
    saved = first.path.read_bytes()
    assert resume_rerun_nodes(first.document) == ["test"]

    resumed = await resume(base, registered, fake, first.document)

    assert resumed.status == "passed"
    assert resumed.document["run_id"] != first.document["run_id"]
    assert resumed.document["resumed_from"] == {
        "run_id": first.document["run_id"],
        "rerun_nodes": ["test"],
        "reused_runs": 1,
        "not_reused_runs": 0,
    }
    # The finished build is reused, not run again; the failed test and what follows run.
    assert fake.calls == {"build": 1, "test": 2, "ship": 1}
    runs = {record["node_id"]: record for record in resumed.document["runs"]}
    assert runs["build"]["reused_from_run_id"] == first.document["run_id"]
    assert runs["build"]["output"]["stdout"] == "build-1"
    assert "reused_from_run_id" not in runs["test"] and "reused_from_run_id" not in runs["ship"]
    reused = [e for e in resumed.document["events"] if e.get("reused")]
    assert [(e["type"], e["node_id"]) for e in reused] == [("node_completed", "build")]
    assert resumed.document["latest_node_outputs"]["build"]["stdout"] == "build-1"
    # The earlier run's record is never changed.
    assert first.path.read_bytes() == saved


@pytest.mark.anyio
async def test_a_stopped_run_reruns_the_node_it_cut_off(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    fake = Fake()
    fake.block = {"test"}
    cancel = threading.Event()
    result: dict[str, Any] = {}

    async def execute() -> None:
        result["run"] = await run(base, registered, fake, cancel_event=cancel)

    async with anyio.create_task_group() as group:
        group.start_soon(execute)
        with anyio.fail_after(5):
            while not fake.started.is_set():
                await anyio.sleep(0.01)
        cancel.set()
    stopped = result["run"]
    assert stopped.status == "stopped"
    assert resume_rerun_nodes(stopped.document) == ["test"]
    assert resume_refusal(stopped.document, stopped.compiled.ir) is None

    fake.block = set()
    resumed = await resume(base, registered, fake, stopped.document)
    assert resumed.status == "passed"
    assert fake.calls == {"build": 1, "test": 2, "ship": 1}


@pytest.mark.anyio
async def test_an_interrupted_run_resumes(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    fake = Fake()
    fake.fail["ship"] = 1
    first = await run(base, registered, fake)
    # Rewrite it as a run whose runner died after `test` started shipping.
    document = json.loads(first.path.read_text())
    document["status"] = "running"
    document["runner_pid"] = 2**22 + 12345
    document["error"] = None
    document["runs"] = [r for r in document["runs"] if r["node_id"] != "ship"]
    document["events"] = [
        e
        for e in document["events"]
        if e["type"] != "workflow_completed"
        and not (e["type"] == "node_completed" and e.get("node_id") == "ship")
    ]
    # `ship` started but never finished; it may have partly run.
    assert resume_rerun_nodes(document) == ["ship"]
    assert "still going" in str(resume_refusal(document, first.compiled.ir))
    # Readers judge liveness when they read a record; its runner is gone.
    document["status"] = effective_run_status(document)
    assert document["status"] == "interrupted"
    assert resume_refusal(document, first.compiled.ir) is None

    resumed = await resume(base, registered, fake, document)
    assert resumed.status == "passed"
    assert fake.calls == {"build": 1, "test": 1, "ship": 2}


JOIN = """Rattish: 1
Workflow:
  name: Join
Node plan:
  type: bash-command
  command: plan
  to:
    - api
    - ui
Node api:
  type: bash-command
  command: api
  needs: plan
  to: review
Node ui:
  type: bash-command
  command: ui
  needs: plan
  to: review
Node review:
  type: bash-command
  command: review
  needs:
    - api
    - ui
"""


@pytest.mark.anyio
async def test_a_join_waits_for_the_rerun_branch(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path, JOIN)
    fake = Fake()
    fake.fail["ui"] = 1
    first = await run(base, registered, fake)
    assert first.status == "failed"
    resumed = await resume(base, registered, fake, first.document)
    assert resumed.status == "passed"
    assert fake.calls["plan"] == 1 and fake.calls["ui"] == 2 and fake.calls["review"] == 1
    # `api` may not have finished before `ui` failed. If it did, it is reused.
    api = [r for r in resumed.document["runs"] if r["node_id"] == "api"]
    finished = any(r["node_id"] == "api" for r in first.document["runs"])
    assert len(api) == 1 and bool(api[0].get("reused_from_run_id")) == finished
    assert [r["node_id"] for r in resumed.document["runs"]].count("review") == 1


CYCLE = """Rattish: 1
Workflow:
  name: Review cycle
Node implement:
  type: bash-command
  command: implement
  to: review
Node review:
  type: bash-command
  command: review
  needs: implement
  to:
    - implement when node.review.output["exit_code"] == 1
    - complete when node.review.output["exit_code"] == 0
Node complete:
  type: bash-command
  command: complete
  needs: review
  finish: pass
"""


@pytest.mark.anyio
async def test_a_cycle_replays_each_pass_in_order(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path, CYCLE)
    reviews: Counter[str] = Counter()
    seen: list[str] = []

    class Reviewer(Fake):
        async def __call__(self, node, context, bindings) -> HandlerResult:
            if node["id"] != "review":
                if node["id"] == "complete" and self.fail["complete"]:
                    return await super().__call__(node, context, bindings)
                return await super().__call__(node, context, bindings)
            self.calls["review"] += 1
            reviews["review"] += 1
            seen.append(context.node_outputs["implement"]["stdout"])
            result = done("reviewed")
            # Ask for one more pass the first time only.
            result["exit_code"] = 1 if reviews["review"] == 1 else 0
            return HandlerResult(True, result)

    fake = Reviewer()
    fake.fail["complete"] = 1
    first = await run(base, registered, fake)
    assert first.status == "failed"
    assert [r["node_id"] for r in first.document["runs"]] == [
        "implement",
        "review",
        "implement",
        "review",
        "complete",
    ]

    resumed = await resume(base, registered, fake, first.document)
    assert resumed.status == "passed"
    # Both passes are reused in order; only the failed terminal node runs again.
    assert fake.calls == {"implement": 2, "review": 2, "complete": 2}
    assert seen == ["implement-1", "implement-2"]
    reused = [r["node_id"] for r in resumed.document["runs"] if r.get("reused_from_run_id")]
    assert reused == ["implement", "review", "implement", "review"]


LOOP = """Rattish: 1
Workflow:
  name: Loop
Node repeat:
  type: loop
  source: {"type": "count", "count": 3}
  to: capture
Node capture:
  type: bash-command
  command: capture
  needs:
    - repeat
"""


@pytest.mark.anyio
async def test_loop_iterations_that_finished_are_reused(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path, LOOP)
    fake = Fake()
    fake.fail["capture"] = 1
    first = await run(base, registered, fake)
    assert first.status == "failed"
    finished = [r for r in first.document["runs"] if r["node_id"] == "capture"]
    resumed = await resume(base, registered, fake, first.document)
    assert resumed.status == "passed"
    captures = [r for r in resumed.document["runs"] if r["node_id"] == "capture"]
    assert len(captures) == 3
    reused = [r for r in captures if r.get("reused_from_run_id")]
    assert len(reused) == sum(1 for r in finished if r["outcome"] == "success")
    # Every iteration ran successfully exactly once across both runs.
    assert fake.calls["capture"] == len(finished) + (3 - len(reused))


@pytest.mark.anyio
async def test_runs_replay_cannot_rebuild_are_refused(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    fake = Fake()
    passed = await run(base, registered, fake)
    assert resume_refusal(passed.document, passed.compiled.ir) == (
        "This run finished successfully, so there is nothing to resume."
    )
    with pytest.raises(RattishResumeRefused, match="nothing to resume"):
        await resume(base, registered, fake, passed.document)

    fake.fail["test"] = 1
    failed = await run(base, registered, fake)
    ir = failed.compiled.ir
    document = failed.document
    assert "inputs" in str(resume_refusal({**document, "input_names": ["target"]}, ir))
    assert "event" in str(resume_refusal({**document, "trigger_event_count": 1}, ir))
    assert "still going" in str(
        resume_refusal({**document, "status": "running", "runner_pid": os.getppid()}, ir)
    )
    built = {**document, "error": {**document["error"], "kind": "budget"}}
    assert "how the workflow is built" in str(resume_refusal(built, ir))
    assert "older version" in str(resume_refusal({**document, "compiled_snapshot": None}, ir))
    assert "errors now" in str(resume_refusal(document, None))

    # The source changed after the run: its results may not fit.
    registered.entrypoint.write_text(LINEAR.replace("command: ship", "command: deploy"))
    with pytest.raises(RattishResumeRefused, match="changed after this run"):
        await resume(base, registered, fake, document)
    registered.entrypoint.write_text(LINEAR)

    # Resume the newest run, not one that was already resumed.
    fake.fail["ship"] = 1
    child = await resume(base, registered, fake, document)
    assert child.status == "failed"
    with pytest.raises(
        RattishResumeRefused, match=f"already resumed as run {child.document['run_id']}"
    ):
        await resume(base, registered, fake, document)
    grandchild = await resume(base, registered, fake, child.document)
    assert grandchild.status == "passed"
    assert fake.calls["build"] == 2  # the passed run and the failed run; resumes reuse it


@pytest.mark.anyio
async def test_agents_with_run_memory_are_refused(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    fake = Fake()
    fake.fail["test"] = 1
    failed = await run(base, registered, fake)
    ir = json.loads(json.dumps(failed.compiled.ir))
    ir["nodes"][0]["configuration"]["memory"] = "run"
    assert "remembers earlier turns" in str(resume_refusal(failed.document, ir))


def test_the_cli_resumes_a_failed_run(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from gofer.cli.main import app

    flag = tmp_path / "ready"
    base, registered = workflow(
        tmp_path,
        "Rattish: 1\nWorkflow:\n  name: CLI resume\n"
        "Node first:\n  type: bash-command\n  command: printf first >> runs.txt\n  to: second\n"
        f"Node second:\n  type: bash-command\n  command: test -f {flag}\n  needs: first\n",
    )
    runner = CliRunner()
    arguments = ["--data-dir", str(base)]
    failed = runner.invoke(app, ["workflow", "run", registered.workflow_id, *arguments])
    assert failed.exit_code == 1
    [path] = (registered.workflow_root / "logs").glob("*.json")
    earlier = json.loads(path.read_text())["run_id"]

    options = runner.invoke(
        app,
        ["workflow", "resume", registered.workflow_id, "--run-id", earlier, "--force", *arguments],
    )
    assert options.exit_code == 1 and "resume where they stopped" in options.output

    flag.write_text("")
    resumed = runner.invoke(
        app, ["workflow", "resume", registered.workflow_id, "--run-id", earlier, *arguments]
    )
    assert resumed.exit_code == 0, resumed.output
    # The finished first step was reused, so it wrote its line once.
    assert (registered.project_root / "runs.txt").read_text() == "first"
    again = runner.invoke(
        app, ["workflow", "resume", registered.workflow_id, "--run-id", earlier, *arguments]
    )
    assert again.exit_code == 1 and "already resumed" in again.output


@pytest.mark.anyio
async def test_a_resume_that_never_started_does_not_block_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gofer.rattish.run_service as run_service
    import gofer.ui.api as api

    base, registered = workflow(tmp_path)
    fake = Fake()
    fake.fail["test"] = 1
    first = await run(base, registered, fake)
    assert first.status == "failed"
    earlier = first.document["run_id"]

    class NotReady:
        ready = False
        diagnostics = ()

    # Pre-run checks fail, as when a provider isn't signed in: no step starts.
    real_preflight = run_service.run_preflight
    monkeypatch.setattr(run_service, "run_preflight", lambda *args, **kwargs: NotReady())
    attempt = await resume(base, registered, fake, first.document)
    assert attempt.status == "preflight_failed"
    assert attempt.document["resumed_from"]["run_id"] == earlier
    assert fake.calls == {"build": 1, "test": 1}

    # The attempt redid nothing, so the earlier run can still be resumed.
    assert run_service.resumed_child(attempt.path.parent, earlier) is None
    state = api.rattish_resume_state_payload(registered.workflow_id, earlier, base)
    assert state == {"allowed": True, "reason": "", "rerunNodes": ["test"]}
    listed = api.list_workflow_run_logs_payload(registered.workflow_id, base)["runs"]
    assert {run["id"]: run["resume"]["allowed"] for run in listed} == {
        earlier: True,
        attempt.document["run_id"]: False,
    }

    monkeypatch.setattr(run_service, "run_preflight", real_preflight)
    resumed = await resume(base, registered, fake, first.document)
    assert resumed.status == "passed"
    assert fake.calls == {"build": 1, "test": 2, "ship": 1}
    # A resume that ran its steps does block resuming the earlier run again.
    assert run_service.resumed_child(resumed.path.parent, earlier) == resumed.document["run_id"]
    with pytest.raises(RattishResumeRefused, match="already resumed"):
        await resume(base, registered, fake, first.document)


HOLD_RESUME = """
import sys
from pathlib import Path
from gofer.rattish.run_service import _resume_claim

with _resume_claim(Path(sys.argv[1]), (sys.argv[2], sys.argv[3])):
    print("claimed", flush=True)
    sys.stdin.readline()
"""


@pytest.mark.anyio
async def test_a_run_another_process_is_resuming_is_refused(tmp_path: Path) -> None:
    import subprocess
    import sys

    base, registered = workflow(tmp_path)
    fake = Fake()
    fake.fail["test"] = 1
    first = await run(base, registered, fake)
    assert first.status == "failed"
    workflow_id, run_id = registered.workflow_id, first.document["run_id"]
    # Another Raticode process, such as the CLI next to the desktop, holds the resume.
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD_RESUME, str(base), workflow_id, run_id],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(map(str, sys.path))},
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "claimed"
        with pytest.raises(RattishResumeRefused, match="already being resumed"):
            await resume(base, registered, fake, first.document)
        assert fake.calls == {"build": 1, "test": 1}
    finally:
        holder.communicate("\n", timeout=30)

    # The claim ends with its process, so the run can be resumed afterwards.
    resumed = await resume(base, registered, fake, first.document)
    assert resumed.status == "passed"
