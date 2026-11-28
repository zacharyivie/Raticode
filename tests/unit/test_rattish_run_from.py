"""Run from a node: reuse unchanged upstream results, run the node and what follows."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import anyio
import pytest

from gofer.core.approvals import ApprovalStore
from gofer.rattish.artifacts import compile_rattish_file
from gofer.rattish.run_from import (
    approval_gates,
    enclosing_loop,
    node_fingerprints,
    reused_nodes,
    run_from_refusal,
)
from gofer.rattish.run_service import (
    RattishResumeRefused,
    RattishRunFromRefused,
    RattishRunResult,
    rattish_run_from_preview,
    resume_rattish_run,
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

LOOP = """Rattish: 1
Workflow:
  name: Loop
Node prepare:
  type: bash-command
  command: prepare
  to: repeat
Node repeat:
  type: loop
  needs: prepare
  source: {"type": "count", "count": 2}
  to: capture
Node capture:
  type: bash-command
  command: capture
  needs:
    - repeat
"""

WITH_PASSWORD = """Rattish: 1
Workflow:
  name: Deploy
  inputs:
    target:
      schema: {"type": "string"}
      required: true
    token:
      schema: {"type": "string", "format": "password"}
      required: false
Node build:
  type: bash-command
  command: build
  to: ship
Node ship:
  type: bash-command
  command: ship
  needs: build
"""

GATED = """Rattish: 1
Workflow:
  name: Gated release
Node build:
  type: bash-command
  command: build
  to: approve
Node approve:
  type: approval-gate
  with:
    built: node.build.output.stdout
  message: Ship {{built}}?
  needs: build
  to:
    - ship when node.approve.output.approved == true
    - hold otherwise
Node ship:
  type: bash-command
  command: ship
  needs: approve
  to: notify
Node hold:
  type: bash-command
  command: hold
  needs: approve
Node notify:
  type: bash-command
  command: notify
  needs: ship
"""

SIGNOFF_BRANCH = """Rattish: 1
Workflow:
  name: Sign-off branch
Node plan:
  type: bash-command
  command: plan
  to:
    - api
    - signoff
Node api:
  type: bash-command
  command: api
  needs: plan
  to: review
Node signoff:
  type: approval-gate
  message: Sign off the plan?
  needs: plan
  to: ui
Node ui:
  type: bash-command
  command: ui
  needs: signoff
  to: review
Node review:
  type: bash-command
  command: review
  needs:
    - api
    - ui
"""


class Fake:
    """A bash-command stand-in that counts calls, records inputs and fails on demand."""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()
        self.fail: Counter[str] = Counter()
        self.inputs: list[tuple[str, dict[str, Any]]] = []
        self.reviews = 0

    async def __call__(self, node, context, bindings) -> HandlerResult:
        node_id = node["id"]
        self.calls[node_id] += 1
        self.inputs.append((node_id, dict(context.workflow_inputs)))
        result = {
            "stdout": f"{node_id}-{self.calls[node_id]}",
            "stderr": "",
            "exit_code": 0,
            "truncated": False,
        }
        if node_id == "review" and "implement" in context.node_outputs:
            # The review cycle: ask for one more pass the first time only.
            self.reviews += 1
            result["exit_code"] = 1 if self.reviews == 1 else 0
        if self.fail[node_id]:
            self.fail[node_id] -= 1
            return HandlerResult(False, {**result, "exit_code": 1})
        return HandlerResult(True, result)

    def handlers(self) -> NodeHandlerRegistry:
        return NodeHandlerRegistry(
            {**DEFAULT_NODE_HANDLERS._handlers, "raticode.bash_command": self}
        )


def workflow(tmp_path: Path, source: str = LINEAR) -> tuple[Path, RegisteredWorkflow]:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    base = tmp_path / "data"
    registered = create_registered_workflow(project, "Run from check", registry_dir=base)
    registered.entrypoint.write_text(source, encoding="utf-8")
    return base, registered


async def run(
    base: Path, registered: RegisteredWorkflow, fake: Fake, **options: Any
) -> RattishRunResult:
    return await run_rattish_file(
        registered.entrypoint, data_dir=base, handlers=fake.handlers(), **options
    )


def ir_of(base: Path, registered: RegisteredWorkflow) -> dict[str, Any]:
    return compile_rattish_file(registered.entrypoint, data_dir=base).ir


async def run_answering(
    base: Path,
    registered: RegisteredWorkflow,
    fake: Fake,
    answers: list[str],
    **options: Any,
) -> tuple[RattishRunResult, list[tuple[str, str]]]:
    """Run, answering each approval gate as it asks with the next of ``answers``.

    Returns the run and the (node, message) of every gate that asked, in order.
    """
    store = ApprovalStore(base)
    asked: list[tuple[str, str]] = []
    holder: dict[str, RattishRunResult] = {}

    async def go() -> None:
        holder["result"] = await run(base, registered, fake, **options)

    with anyio.fail_after(10):
        async with anyio.create_task_group() as group:
            group.start_soon(go)
            remaining = list(answers)
            while "result" not in holder:
                pending = store.list_pending()
                if not pending:
                    await anyio.sleep(0.01)
                    continue
                request = pending[0]
                asked.append((request.node_id, request.message))
                assert remaining, f"Gate {request.node_id!r} asked with no answer left."
                store.decide(
                    request.workflow_id,
                    request.run_id,
                    request.node_id,
                    remaining.pop(0),  # type: ignore[arg-type]
                    decided_by="owner",
                )
    assert not remaining, f"Unused answers: {remaining}"
    return holder["result"], asked


@pytest.mark.anyio
async def test_an_edited_node_runs_again_with_unchanged_upstream_results(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    fake = Fake()
    first = await run(base, registered, fake)
    assert first.status == "passed"
    saved = first.path.read_bytes()

    registered.entrypoint.write_text(LINEAR.replace("command: ship", "command: deploy"))
    again = await run(base, registered, fake, from_node="ship")

    assert again.status == "passed"
    # build and test are reused; ship runs from the edited source.
    assert fake.calls == {"build": 1, "test": 1, "ship": 2}
    assert again.document["replayed_from"] == {
        "run_id": first.document["run_id"],
        "node_id": "ship",
        "reused_nodes": ["build", "test"],
        "reused_runs": 2,
        "not_reused_runs": 0,
    }
    assert "resumed_from" not in again.document
    runs = {record["node_id"]: record for record in again.document["runs"]}
    for node_id in ("build", "test"):
        assert runs[node_id]["reused_from_run_id"] == first.document["run_id"]
        # Reused steps made no call: no usage of their own, so $0 and no limit counted.
        assert "usage" not in runs[node_id]
    assert runs["build"]["output"]["stdout"] == "build-1"
    assert "reused_from_run_id" not in runs["ship"]
    reused = [e["node_id"] for e in again.document["events"] if e.get("reused")]
    assert reused == ["build", "test"]
    assert again.document["usage"]["calls"] == 0
    # The earlier run's record is never changed.
    assert first.path.read_bytes() == saved


@pytest.mark.anyio
async def test_a_changed_upstream_node_is_refused_by_name(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    fake = Fake()
    first = await run(base, registered, fake)

    registered.entrypoint.write_text(LINEAR.replace("command: test", "command: check"))
    with pytest.raises(RattishRunFromRefused, match="Node 'test', upstream of 'ship', changed"):
        await run(base, registered, fake, from_node="ship")
    with pytest.raises(RattishRunFromRefused, match="Node 'test'"):
        await run(base, registered, fake, from_node="ship", reuse_run_id=first.document["run_id"])
    # Running from the changed node itself is fine: only build is upstream of it.
    from_test = await run(base, registered, fake, from_node="test")
    assert from_test.status == "passed"
    assert from_test.document["replayed_from"]["reused_nodes"] == ["build"]
    assert fake.calls == {"build": 1, "test": 2, "ship": 2}
    # The refusal is a resume-style refusal, so existing callers report it the same way.
    assert issubclass(RattishRunFromRefused, RattishResumeRefused)


@pytest.mark.anyio
async def test_the_newest_run_that_fits_is_picked(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    fake = Fake()
    original = await run(base, registered, fake)
    registered.entrypoint.write_text(LINEAR.replace("command: test", "command: check"))
    changed = await run(base, registered, fake)
    assert changed.status == "passed"

    # Back to the original test step: the newer run used another one, so the older run fits.
    registered.entrypoint.write_text(LINEAR)
    preview = rattish_run_from_preview(registered.entrypoint, "ship", data_dir=base)
    assert preview["reuseRunId"] == original.document["run_id"]
    assert preview["reusedNodes"] == ["build", "test"]
    assert preview["needsInputs"] == []
    again = await run(base, registered, fake, from_node="ship")
    assert again.document["replayed_from"]["run_id"] == original.document["run_id"]

    # Naming a run reuses that one, or says why it can't.
    with pytest.raises(RattishRunFromRefused, match="Node 'test'"):
        await run(base, registered, fake, from_node="ship", reuse_run_id=changed.document["run_id"])
    with pytest.raises(RattishRunFromRefused, match="was not found"):
        await run(base, registered, fake, from_node="ship", reuse_run_id="20990101T000000Z-x")
    with pytest.raises(RattishRunFromRefused, match="Invalid run ID"):
        await run(base, registered, fake, from_node="ship", reuse_run_id="../escape")


@pytest.mark.anyio
async def test_a_run_that_never_reached_the_node_is_not_reused(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    fake = Fake()
    with pytest.raises(RattishRunFromRefused, match="No earlier run reached node 'ship'"):
        await run(base, registered, fake, from_node="ship")
    fake.fail["test"] = 1
    failed = await run(base, registered, fake)
    assert failed.status == "failed"
    with pytest.raises(RattishRunFromRefused, match="No earlier run reached node 'ship'"):
        await run(base, registered, fake, from_node="ship")
    with pytest.raises(RattishRunFromRefused, match="never reached node 'ship'"):
        await run(base, registered, fake, from_node="ship", reuse_run_id=failed.document["run_id"])
    # A failed run that reached the node can be run from it.
    from_test = await run(base, registered, fake, from_node="test")
    assert from_test.status == "passed"
    assert fake.calls["build"] == 1
    with pytest.raises(RattishRunFromRefused, match="has no node 'nope'"):
        await run(base, registered, fake, from_node="nope")


@pytest.mark.anyio
async def test_a_branch_beside_the_node_runs_again(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path, JOIN)
    fake = Fake()
    await run(base, registered, fake)
    again = await run(base, registered, fake, from_node="ui")
    assert again.status == "passed"
    # Only plan is upstream of ui; api isn't, so it runs again with ui and review.
    assert again.document["replayed_from"]["reused_nodes"] == ["plan"]
    assert fake.calls == {"plan": 1, "api": 2, "ui": 2, "review": 2}
    review = await run(base, registered, fake, from_node="review")
    assert review.document["replayed_from"]["reused_nodes"] == ["api", "plan", "ui"]
    assert fake.calls["review"] == 3 and fake.calls["api"] == 2


@pytest.mark.anyio
async def test_a_cycle_before_the_node_is_replayed_in_order(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path, CYCLE)
    fake = Fake()
    first = await run(base, registered, fake)
    assert [r["node_id"] for r in first.document["runs"]] == [
        "implement",
        "review",
        "implement",
        "review",
        "complete",
    ]
    again = await run(base, registered, fake, from_node="complete")
    assert again.status == "passed"
    assert fake.calls == {"implement": 2, "review": 2, "complete": 2}
    reused = [r["node_id"] for r in again.document["runs"] if r.get("reused_from_run_id")]
    assert reused == ["implement", "review", "implement", "review"]
    # Nodes in a cycle through the chosen node depend on it, so nothing is reused.
    ir = ir_of(base, registered)
    assert reused_nodes(ir, "review") == []


@pytest.mark.anyio
async def test_a_node_inside_a_loop_body_is_refused(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path, LOOP)
    fake = Fake()
    await run(base, registered, fake)
    ir = ir_of(base, registered)
    assert enclosing_loop(ir, "capture") == "repeat"
    assert enclosing_loop(ir, "repeat") is None
    assert "inside loop 'repeat'" in str(run_from_refusal(ir, "capture"))
    with pytest.raises(RattishRunFromRefused, match="inside loop 'repeat'"):
        await run(base, registered, fake, from_node="capture")
    # The loop node itself can be run from: its iterations run again.
    again = await run(base, registered, fake, from_node="repeat")
    assert again.status == "passed"
    assert fake.calls == {"prepare": 1, "capture": 4}


@pytest.mark.anyio
async def test_password_inputs_are_asked_for_again(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path, WITH_PASSWORD)
    fake = Fake()
    await run(base, registered, fake, workflow_inputs={"target": "prod", "token": "s3cret"})
    preview = rattish_run_from_preview(registered.entrypoint, "ship", data_dir=base)
    assert preview["needsInputs"] == ["token"]

    with pytest.raises(RattishRunFromRefused, match="Enter token again"):
        await run(base, registered, fake, from_node="ship")
    with pytest.raises(RattishRunFromRefused, match="uses the reused run's inputs only"):
        await run(base, registered, fake, from_node="ship", workflow_inputs={"target": "dev"})
    again = await run(base, registered, fake, from_node="ship", resume_inputs={"token": "n3w"})
    assert again.status == "passed"
    # The reused run's saved inputs, with the password given again.
    assert fake.inputs[-1] == ("ship", {"target": "prod", "token": "n3w"})
    assert fake.calls == {"build": 1, "ship": 2}


@pytest.mark.anyio
async def test_a_run_from_a_node_does_not_count_as_resuming_its_source(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    fake = Fake()
    fake.fail["ship"] = 1
    failed = await run(base, registered, fake)
    from_ship = await run(base, registered, fake, from_node="ship")
    assert from_ship.status == "passed"
    # The failed run can still be resumed: the run from ship isn't its resume.
    resumed = await resume_rattish_run(
        registered.entrypoint, failed.document, data_dir=base, handlers=fake.handlers()
    )
    assert resumed.status == "passed"


def test_fingerprints_cover_dependencies_but_not_positions(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    before = node_fingerprints(ir_of(base, registered), ["build", "test", "ship"])
    # Moving nodes around the file changes no fingerprint.
    registered.entrypoint.write_text(
        LINEAR.replace("Node build:", "\n\nNode build:"), encoding="utf-8"
    )
    moved = node_fingerprints(ir_of(base, registered), ["build", "test", "ship"])
    assert moved == before
    # Changing build changes it and everything depending on it.
    registered.entrypoint.write_text(LINEAR.replace("command: build", "command: make"))
    edited = node_fingerprints(ir_of(base, registered), ["build", "test", "ship"])
    assert all(edited[node] != before[node] for node in ("build", "test", "ship"))
    # Changing ship changes only ship's.
    registered.entrypoint.write_text(LINEAR.replace("command: ship", "command: deploy"))
    edited = node_fingerprints(ir_of(base, registered), ["build", "test", "ship"])
    assert edited["build"] == before["build"] and edited["test"] == before["test"]
    assert edited["ship"] != before["ship"]


def test_the_cli_runs_from_a_node(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from gofer.cli.main import app

    base, registered = workflow(
        tmp_path,
        "Rattish: 1\nWorkflow:\n  name: CLI run from\n"
        "Node first:\n  type: bash-command\n  command: printf first >> runs.txt\n  to: second\n"
        "Node second:\n  type: bash-command\n  command: printf second >> runs.txt\n"
        "  needs: first\n",
    )
    runner = CliRunner()
    source = str(registered.entrypoint)
    arguments = ["--data-dir", str(base)]
    refused = runner.invoke(app, ["rattish", "run", source, "--from", "second", *arguments])
    assert refused.exit_code == 1 and "No earlier run reached node 'second'" in refused.output
    first = runner.invoke(app, ["rattish", "run", source, "--format", "json", *arguments])
    assert first.exit_code == 0, first.output
    earlier = json.loads(first.output)["run"]["run_id"]

    again = runner.invoke(
        app, ["rattish", "run", source, "--from", "second", "--format", "json", *arguments]
    )
    assert again.exit_code == 0, again.output
    document = json.loads(again.output)["run"]
    assert document["replayed_from"]["run_id"] == earlier
    assert document["replayed_from"]["reused_nodes"] == ["first"]
    named = runner.invoke(
        app, ["rattish", "run", source, "--from", "second", "--reuse", earlier, *arguments]
    )
    assert named.exit_code == 0, named.output
    assert "Reused 1 step from run " + earlier in named.output
    # first was reused twice, so it wrote its line once; second ran three times.
    assert (registered.project_root / "runs.txt").read_text() == "firstsecondsecondsecond"

    reuse_alone = runner.invoke(app, ["rattish", "run", source, "--reuse", earlier, *arguments])
    assert reuse_alone.exit_code == 2 and "--reuse needs --from" in reuse_alone.output


@pytest.mark.anyio
async def test_the_desktop_api_previews_and_runs_from_a_node(tmp_path: Path) -> None:
    from gofer.ui import api

    base, registered = workflow(
        tmp_path,
        "Rattish: 1\nWorkflow:\n  name: API run from\n"
        "Node first:\n  type: bash-command\n  command: printf first >> runs.txt\n  to: second\n"
        "Node second:\n  type: bash-command\n  command: printf second >> runs.txt\n"
        "  needs: first\n",
    )
    workflow_id = registered.workflow_id
    with pytest.raises(api.WorkflowRunError, match="No earlier run reached node 'second'"):
        await api.run_workflow_payload(workflow_id, base, dry_run=True, from_node="second")
    first = await api.run_workflow_payload(workflow_id, base)
    earlier = first["rattishRun"]["run_id"]

    plan = await api.run_workflow_payload(workflow_id, base, dry_run=True, from_node="Second")
    assert plan["fromNode"] == "second"
    assert plan["reuseRunId"] == earlier
    assert plan["reusedNodes"] == ["first"]
    assert plan["needsInputs"] == []
    assert (registered.project_root / "runs.txt").read_text() == "firstsecond"

    again = await api.run_workflow_payload(
        workflow_id,
        base,
        from_node="second",
        reuse_run_id=earlier,
        expected_revision=plan["revision"],
    )
    assert again["replayedFrom"] == {
        "runId": earlier,
        "nodeId": "second",
        "reusedNodes": ["first"],
        "reusedRuns": 1,
    }
    # The Run Timeline marks the replayed step Reused, with the run it came from.
    assert again["runNodes"]["first"]["reused"] is True
    assert again["runNodes"]["second"]["reused"] is False
    [reused] = [event for event in again["runEvents"] if event["status"] == "reused"]
    assert reused["nodeId"] == "first" and reused["reused"] is True
    assert f"reused its saved result from run {earlier}" in reused["message"]
    assert "$0" in reused["message"]
    assert (registered.project_root / "runs.txt").read_text() == "firstsecondsecond"
    with pytest.raises(api.WorkflowRunError, match="reuseRunId needs fromNode"):
        await api.run_workflow_payload(workflow_id, base, reuse_run_id=earlier)

    page = api.list_workflow_run_logs_payload(workflow_id, base)
    summary = next(run for run in page["runs"] if run["id"] == again["rattishRun"]["run_id"])
    assert summary["replayedFrom"]["runId"] == earlier


@pytest.mark.anyio
async def test_an_approval_gate_before_the_node_asks_again(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path, GATED)
    fake = Fake()
    first, asked = await run_answering(base, registered, fake, ["approved"])
    assert first.status == "passed"
    assert asked == [("approve", "Ship build-1?")]

    ir = ir_of(base, registered)
    assert approval_gates(ir) == {"approve"}
    # build is before the gate, so it's reused; the gate and ship, which depends on it,
    # run again even though they're upstream of notify.
    assert reused_nodes(ir, "notify") == ["build"]
    preview = rattish_run_from_preview(registered.entrypoint, "notify", data_dir=base)
    assert preview["reusedNodes"] == ["build"]
    assert preview["askAgainNodes"] == ["approve"]

    again, asked = await run_answering(base, registered, fake, ["approved"], from_node="notify")
    assert again.status == "passed"
    # The gate asked again with the same message, built from build's reused result.
    assert asked == [("approve", "Ship build-1?")]
    assert fake.calls == {"build": 1, "ship": 2, "notify": 2}
    assert again.document["replayed_from"]["reused_nodes"] == ["build"]
    assert again.document["replayed_from"]["reused_runs"] == 1
    runs = {record["node_id"]: record for record in again.document["runs"]}
    assert runs["build"]["reused_from_run_id"] == first.document["run_id"]
    for node_id in ("approve", "ship", "notify"):
        assert "reused_from_run_id" not in runs[node_id]
    # Its answer is a new decision on the new run, not the earlier run's.
    store = ApprovalStore(base)
    workflow_id = again.document["workflow"]["id"]
    request = store.get(workflow_id, again.document["run_id"], "approve")
    assert request is not None and request.decision is not None
    assert request.decision.decision == "approved"


@pytest.mark.anyio
async def test_an_approval_gate_on_a_parallel_branch_asks_again(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path, SIGNOFF_BRANCH)
    fake = Fake()
    first, asked = await run_answering(base, registered, fake, ["approved"])
    assert first.status == "passed"
    assert [node for node, _ in asked] == ["signoff"]

    # From the join: the plain branch is reused, the sign-off branch runs again.
    ir = ir_of(base, registered)
    assert reused_nodes(ir, "review") == ["api", "plan"]
    preview = rattish_run_from_preview(registered.entrypoint, "review", data_dir=base)
    assert preview["askAgainNodes"] == ["signoff"]
    again, asked = await run_answering(base, registered, fake, ["approved"], from_node="review")
    assert again.status == "passed"
    assert asked == [("signoff", "Sign off the plan?")]
    assert fake.calls == {"plan": 1, "api": 1, "ui": 2, "review": 2}
    assert again.document["replayed_from"]["reused_nodes"] == ["api", "plan"]

    # From the other branch, the gate beside it isn't upstream, so it runs again and
    # asks; the preview lists it too.
    assert reused_nodes(ir, "api") == ["plan"]
    preview = rattish_run_from_preview(registered.entrypoint, "api", data_dir=base)
    assert preview["askAgainNodes"] == ["signoff"]
    beside, asked = await run_answering(base, registered, fake, ["approved"], from_node="api")
    assert beside.status == "passed"
    assert [node for node, _ in asked] == ["signoff"]
    assert fake.calls == {"plan": 1, "api": 2, "ui": 3, "review": 3}


@pytest.mark.anyio
async def test_an_earlier_rejected_gate_asks_again_and_follows_the_new_answer(
    tmp_path: Path,
) -> None:
    base, registered = workflow(tmp_path, GATED)
    fake = Fake()
    rejected, asked = await run_answering(base, registered, fake, ["rejected"])
    assert rejected.status == "passed"
    assert rejected.document["latest_node_outputs"]["approve"]["approved"] is False
    assert fake.calls == {"build": 1, "hold": 1}

    preview = rattish_run_from_preview(registered.entrypoint, "hold", data_dir=base)
    assert preview["reusedNodes"] == ["build"]
    assert preview["askAgainNodes"] == ["approve"]
    # The earlier rejection isn't reused: the gate asks again, and this time the
    # approval sends the run down the ship branch instead of hold.
    again, asked = await run_answering(base, registered, fake, ["approved"], from_node="hold")
    assert again.status == "passed"
    assert asked == [("approve", "Ship build-1?")]
    assert fake.calls == {"build": 1, "hold": 1, "ship": 1, "notify": 1}
    outputs = again.document["latest_node_outputs"]
    assert outputs["approve"]["approved"] is True
    assert "hold" not in outputs
    assert [r["node_id"] for r in again.document["runs"] if r.get("reused_from_run_id")] == [
        "build"
    ]
