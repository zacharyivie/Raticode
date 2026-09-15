from __future__ import annotations

from collections import Counter
from pathlib import Path

import anyio
import pytest

from gofer.rattish.compiler import CompileContext, RattishCompiler
from gofer.rattish.preflight import run_preflight
from gofer.rattish.runtime import (
    HandlerResult,
    NodeHandlerRegistry,
    RuntimeErrorInfo,
)
from gofer.rattish.workflow_runtime import execute_workflow

PROJECT_ROOT = Path(__file__).parents[2]
RATTISH_ROOT = PROJECT_ROOT / "rattish"


def compiler() -> RattishCompiler:
    return RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=[RATTISH_ROOT / "contracts" / "bash-command.json"],
    )


def compile_source(source: str, tmp_path: Path):
    return compiler().compile(source, CompileContext("workflow-runtime", tmp_path)).ir


def output(stdout: str = "") -> dict[str, object]:
    return {"stdout": stdout, "stderr": "", "exit_code": 0}


@pytest.mark.anyio
async def test_fan_out_join_coalesces_one_activation_group(tmp_path: Path) -> None:
    source = """Rattish: 1

Workflow:
  name: Fan out join

Node plan:
  type: bash-command
  command: plan
  to:
    - implement-api
    - implement-ui

Node implement-api:
  type: bash-command
  command: api
  needs: plan
  to: review

Node implement-ui:
  type: bash-command
  command: ui
  needs: plan
  to: review

Node review:
  type: bash-command
  command: review
  needs:
    - implement-api
    - implement-ui
"""
    calls: list[tuple[str, set[str]]] = []

    async def fake_handler(node, context, bindings):
        _ = bindings
        calls.append((node["id"], set(context.node_outputs)))
        return HandlerResult(True, output(node["id"]))

    result = await execute_workflow(
        compile_source(source, tmp_path),
        handlers=NodeHandlerRegistry({"raticode.bash_command": fake_handler}),
    )

    assert result.outcome == "pass"
    assert Counter(node_id for node_id, _ in calls) == {
        "plan": 1,
        "implement-api": 1,
        "implement-ui": 1,
        "review": 1,
    }
    review_inputs = next(inputs for node_id, inputs in calls if node_id == "review")
    assert {"plan", "implement-api", "implement-ui"} <= review_inputs


@pytest.mark.anyio
@pytest.mark.parametrize(("c_delay", "expected_b_runs"), [(0.03, 1), (0, 2)])
async def test_locked_signals_coalesce_but_later_groups_run_again(
    tmp_path: Path, c_delay: float, expected_b_runs: int
) -> None:
    source = """Rattish: 1

Workflow:
  name: Timed join

Node a:
  type: bash-command
  command: a
  to: b

Node c:
  type: bash-command
  command: c
  to: b

Node b:
  type: bash-command
  command: b
  needs: c
"""
    b_snapshots: list[set[str]] = []

    async def fake_handler(node, context, bindings):
        _ = bindings
        if node["id"] == "a" and c_delay == 0:
            await anyio.sleep(0.03)
        if node["id"] == "c" and c_delay:
            await anyio.sleep(c_delay)
        if node["id"] == "b":
            b_snapshots.append(set(context.node_outputs))
        return HandlerResult(True, output(node["id"]))

    result = await execute_workflow(
        compile_source(source, tmp_path),
        handlers=NodeHandlerRegistry({"raticode.bash_command": fake_handler}),
    )

    assert result.outcome == "pass"
    assert len(b_snapshots) == expected_b_runs
    if expected_b_runs == 1:
        assert {"a", "c"} <= b_snapshots[0]
    else:
        assert "c" in b_snapshots[0] and "a" not in b_snapshots[0]
        assert {"a", "c"} <= b_snapshots[1]


@pytest.mark.anyio
async def test_cycle_uses_latest_successful_output_at_consumer_start(tmp_path: Path) -> None:
    source = """Rattish: 1

Workflow:
  name: Review loop

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
    calls: Counter[str] = Counter()
    reviewed: list[str] = []

    async def fake_handler(node, context, bindings):
        _ = bindings
        node_id = node["id"]
        calls[node_id] += 1
        if node_id == "implement":
            return HandlerResult(True, output(f"revision-{calls[node_id]}"))
        if node_id == "review":
            reviewed.append(context.node_outputs["implement"]["stdout"])
            result = output("reviewed")
            result["exit_code"] = 1 if calls[node_id] == 1 else 0
            return HandlerResult(True, result)
        return HandlerResult(True, output(node_id))

    result = await execute_workflow(
        compile_source(source, tmp_path),
        handlers=NodeHandlerRegistry({"raticode.bash_command": fake_handler}),
    )

    assert result.outcome == "pass"
    assert calls == {"implement": 2, "review": 2, "complete": 1}
    assert reviewed == ["revision-1", "revision-2"]


@pytest.mark.anyio
async def test_public_outputs_resolve_selected_and_structured_values(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Structured public outputs
  outputs:
    result:
      from: node.produce.output
      schema: {
        "type": "object",
        "properties": {
          "stdout": {"type": "string"},
          "stderr": {"type": "string"},
          "exit_code": {"type": "integer"}
        },
        "required": ["stdout", "stderr", "exit_code"],
        "additionalProperties": false
      }
    text:
      from: node.produce.output.stdout
      schema: {"type": "string"}
Node produce:
  type: bash-command
  command: produce
"""

    async def fake_handler(node, context, bindings):
        _ = node, context, bindings
        return HandlerResult(True, output("ready"))

    result = await execute_workflow(
        compile_source(source, tmp_path),
        handlers=NodeHandlerRegistry({"raticode.bash_command": fake_handler}),
    )

    assert result.outcome == "pass"
    assert result.outputs == {"result": output("ready"), "text": "ready"}


@pytest.mark.anyio
async def test_public_output_can_pass_through_a_workflow_input(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Input public output
  inputs:
    message:
      schema: {"type": "string"}
      required: true
  outputs:
    message:
      from: input.message
      schema: {"type": "string"}
Node run:
  type: bash-command
  command: run
"""

    async def fake_handler(node, context, bindings):
        _ = node, context, bindings
        return HandlerResult(True, output("done"))

    result = await execute_workflow(
        compile_source(source, tmp_path),
        workflow_inputs={"message": "hello"},
        handlers=NodeHandlerRegistry({"raticode.bash_command": fake_handler}),
    )

    assert result.outcome == "pass"
    assert result.outputs == {"message": "hello"}


@pytest.mark.anyio
async def test_missing_public_output_fails_instead_of_disappearing(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Missing public output
  outputs:
    result:
      from: node.produce.output.stdout
      schema: {"type": "string"}
Node gate:
  type: bash-command
  command: gate
  to: produce when node.gate.output["exit_code"] == 1
Node produce:
  type: bash-command
  command: produce
  needs: gate
"""

    async def fake_handler(node, context, bindings):
        _ = context, bindings
        return HandlerResult(True, output(node["id"]))

    result = await execute_workflow(
        compile_source(source, tmp_path),
        handlers=NodeHandlerRegistry({"raticode.bash_command": fake_handler}),
    )

    assert result.outcome == "failure"
    assert result.outputs == {}
    assert result.error is not None
    assert result.error.code == "RATTISH_WORKFLOW_OUTPUT_MISSING"
    assert result.error.details["output"] == "result"


@pytest.mark.anyio
async def test_failed_allowed_producer_leaves_public_output_missing(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Failed public output
  outputs:
    result:
      from: node.flaky.output.stdout
      schema: {"type": "string"}
Node flaky:
  type: bash-command
  command: flaky
  allow-fail: true
"""

    async def fake_handler(node, context, bindings):
        _ = node, context, bindings
        return HandlerResult(
            False,
            output(),
            RuntimeErrorInfo("command", "TEST_ALLOWED", "allowed failure"),
        )

    result = await execute_workflow(
        compile_source(source, tmp_path),
        handlers=NodeHandlerRegistry({"raticode.bash_command": fake_handler}),
    )

    assert result.outcome == "failure"
    assert result.error is not None
    assert result.error.code == "RATTISH_WORKFLOW_OUTPUT_MISSING"
    assert result.latest_node_outputs == {}


@pytest.mark.anyio
async def test_allowed_failure_can_publish_its_error_channel(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Public allowed failure
  outputs:
    failure:
      from: node.flaky.error
      schema: {
        "type": "object",
        "properties": {
          "kind": {"type": "string"},
          "code": {"type": "string"},
          "message": {"type": "string"},
          "details": {}
        },
        "required": ["kind", "code", "message"],
        "additionalProperties": false
      }
Node flaky:
  type: bash-command
  command: flaky
  allow-fail: true
"""

    async def fake_handler(node, context, bindings):
        _ = node, context, bindings
        return HandlerResult(
            False,
            output(),
            RuntimeErrorInfo("command", "TEST_ALLOWED", "allowed failure", {"attempt": 1}),
        )

    result = await execute_workflow(
        compile_source(source, tmp_path),
        handlers=NodeHandlerRegistry({"raticode.bash_command": fake_handler}),
    )

    assert result.outcome == "pass"
    assert result.outputs["failure"]["code"] == "TEST_ALLOWED"
    assert result.outputs["failure"]["details"] == {"attempt": 1}


@pytest.mark.anyio
async def test_cyclic_public_output_uses_latest_successful_producer_value(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Cyclic public output
  outputs:
    latest:
      from: node.a.output.stdout
      schema: {"type": "string"}
Node a:
  type: bash-command
  command: a
  to: b
Node b:
  type: bash-command
  command: b
  needs: a
  to: a when node.b.output["exit_code"] == 0
"""
    calls: Counter[str] = Counter()

    async def fake_handler(node, context, bindings):
        _ = context, bindings
        node_id = node["id"]
        calls[node_id] += 1
        result = output(f"{node_id}-{calls[node_id]}")
        if node_id == "b" and calls[node_id] == 2:
            result["exit_code"] = 1
        return HandlerResult(True, result)

    result = await execute_workflow(
        compile_source(source, tmp_path),
        handlers=NodeHandlerRegistry({"raticode.bash_command": fake_handler}),
    )

    assert result.outcome == "pass"
    assert calls == {"a": 2, "b": 2}
    assert result.outputs == {"latest": "a-2"}


@pytest.mark.anyio
async def test_allowed_failure_routes_and_satisfies_needs(tmp_path: Path) -> None:
    source = """Rattish: 1

Workflow:
  name: Allowed failure

Node flaky:
  type: bash-command
  command: flaky
  allow-fail: true
  to: cleanup

Node cleanup:
  type: bash-command
  command: cleanup
  needs: flaky
"""

    async def fake_handler(node, context, bindings):
        _ = context, bindings
        if node["id"] == "flaky":
            return HandlerResult(
                False,
                output(),
                RuntimeErrorInfo("command", "TEST_FAILURE", "expected failure"),
            )
        return HandlerResult(True, output("cleaned"))

    result = await execute_workflow(
        compile_source(source, tmp_path),
        handlers=NodeHandlerRegistry({"raticode.bash_command": fake_handler}),
    )

    assert result.outcome == "pass"
    assert [record.node_id for record in result.runs] == ["flaky", "cleanup"]
    assert result.runs[0].result.outcome == "allowed_failure"


@pytest.mark.anyio
async def test_unresolved_partial_join_fails_at_quiescence(tmp_path: Path) -> None:
    source = """Rattish: 1

Workflow:
  name: Unresolved join

Node a:
  type: bash-command
  command: a
  to: b

Node gate:
  type: bash-command
  command: gate
  to:
    - c when node.gate.output["exit_code"] == 1

Node c:
  type: bash-command
  command: c
  needs: gate

Node b:
  type: bash-command
  command: b
  needs: c
"""

    async def fake_handler(node, context, bindings):
        _ = context, bindings
        return HandlerResult(True, output(node["id"]))

    result = await execute_workflow(
        compile_source(source, tmp_path),
        handlers=NodeHandlerRegistry({"raticode.bash_command": fake_handler}),
    )

    assert result.outcome == "failure"
    assert result.error is not None
    assert result.error.code == "RATTISH_JOIN_UNRESOLVED"
    assert result.error.details["joins"][0]["node_id"] == "b"
    assert result.error.details["joins"][0]["missing_predecessors"] == ["c"]


@pytest.mark.anyio
async def test_fatal_failure_cancels_other_initial_work(tmp_path: Path) -> None:
    source = """Rattish: 1

Workflow:
  name: Fatal failure

Node fail-fast:
  type: bash-command
  command: fail

Node slow:
  type: bash-command
  command: slow
"""
    slow_cancelled = anyio.Event()

    async def fake_handler(node, context, bindings):
        _ = context, bindings
        if node["id"] == "fail-fast":
            return HandlerResult(
                False,
                output(),
                RuntimeErrorInfo("command", "TEST_FATAL", "fatal"),
            )
        try:
            await anyio.sleep(10)
        finally:
            slow_cancelled.set()
        return HandlerResult(True, output("slow"))

    result = await execute_workflow(
        compile_source(source, tmp_path),
        handlers=NodeHandlerRegistry({"raticode.bash_command": fake_handler}),
    )

    assert result.outcome == "failure"
    assert result.error is not None and result.error.code == "TEST_FATAL"
    assert slow_cancelled.is_set()


@pytest.mark.anyio
async def test_workflow_timeout_cancels_running_nodes(tmp_path: Path) -> None:
    source = """Rattish: 1

Workflow:
  name: Workflow timeout
  timeout: 10ms

Node slow:
  type: bash-command
  command: slow
"""

    async def fake_handler(node, context, bindings):
        _ = node, context, bindings
        await anyio.sleep(10)
        return HandlerResult(True, output())

    result = await execute_workflow(
        compile_source(source, tmp_path),
        handlers=NodeHandlerRegistry({"raticode.bash_command": fake_handler}),
    )

    assert result.outcome == "failure"
    assert result.error is not None
    assert result.error.code == "RATTISH_TIMEOUT"
    assert result.error.details["scope"] == "workflow"


@pytest.mark.anyio
async def test_node_timeout_is_an_allowed_failure_when_configured(tmp_path: Path) -> None:
    source = """Rattish: 1

Workflow:
  name: Node timeout

Node slow:
  type: bash-command
  command: slow
  timeout: 10ms
  allow-fail: true
  to: after

Node after:
  type: bash-command
  command: after
  needs: slow
"""

    async def fake_handler(node, context, bindings):
        _ = context, bindings
        if node["id"] == "slow":
            await anyio.sleep(10)
        return HandlerResult(True, output(node["id"]))

    result = await execute_workflow(
        compile_source(source, tmp_path),
        handlers=NodeHandlerRegistry({"raticode.bash_command": fake_handler}),
    )

    assert result.outcome == "pass"
    assert result.runs[0].result.outcome == "allowed_failure"
    assert result.runs[0].result.error is not None
    assert result.runs[0].result.error.code == "RATTISH_TIMEOUT"
    assert result.runs[1].node_id == "after"


@pytest.mark.anyio
async def test_node_max_runs_fails_a_cycle_before_starting_excess_work(tmp_path: Path) -> None:
    source = """Rattish: 1

Workflow:
  name: Bounded cycle

Node repeat:
  type: bash-command
  command: repeat
  max-runs: 2
  to: repeat
"""

    async def fake_handler(node, context, bindings):
        _ = context, bindings
        return HandlerResult(True, output(node["id"]))

    result = await execute_workflow(
        compile_source(source, tmp_path),
        handlers=NodeHandlerRegistry({"raticode.bash_command": fake_handler}),
    )

    assert result.outcome == "failure"
    assert len(result.runs) == 2
    assert result.error is not None
    assert result.error.code == "RATTISH_RUN_LIMIT_EXCEEDED"
    assert result.error.details["scope"] == "node"


@pytest.mark.anyio
async def test_empty_workflow_compiles_but_preflight_and_runtime_reject_it(
    tmp_path: Path,
) -> None:
    source = """Rattish: 1

Workflow:
  name: Empty editor workflow
"""
    ir = compile_source(source, tmp_path)

    preflight = run_preflight(ir)
    result = await execute_workflow(ir)

    assert ir["nodes"] == []
    assert not preflight.ready
    assert [item.code for item in preflight.diagnostics] == ["RATTISH_WORKFLOW_EMPTY"]
    assert preflight.diagnostics[0].phase == "preflight"
    assert result.outcome == "failure"
    assert result.runs == ()
    assert result.error is not None
    assert result.error.code == "RATTISH_WORKFLOW_EMPTY"


@pytest.mark.anyio
async def test_retries_share_one_run_limit_unit_and_receive_fresh_node_timeouts(
    tmp_path: Path,
) -> None:
    source = """Rattish: 1

Workflow:
  name: Retry accounting
  max-runs: 1

Node flaky:
  type: bash-command
  command: flaky
  timeout: 10ms
  retry-count: 1
  retry-delay: 1ms
  max-runs: 1
"""
    attempts = 0

    async def fake_handler(node, context, bindings):
        nonlocal attempts
        _ = node, context, bindings
        attempts += 1
        if attempts == 1:
            await anyio.sleep(10)
        return HandlerResult(True, output("recovered"))

    result = await execute_workflow(
        compile_source(source, tmp_path),
        handlers=NodeHandlerRegistry({"raticode.bash_command": fake_handler}),
    )

    assert result.outcome == "pass"
    assert attempts == 2
    assert len(result.runs) == 1
    assert result.runs[0].run_number == 1


@pytest.mark.anyio
async def test_unconditional_routes_do_not_suppress_otherwise(tmp_path: Path) -> None:
    source = """Rattish: 1

Workflow:
  name: Otherwise routing

Node decide:
  type: bash-command
  command: decide
  to:
    - always
    - never when node.decide.output["exit_code"] == 1
    - fallback otherwise

Node always:
  type: bash-command
  command: always
  needs: decide

Node never:
  type: bash-command
  command: never
  needs: decide

Node fallback:
  type: bash-command
  command: fallback
  needs: decide
"""
    calls: list[str] = []

    async def fake_handler(node, context, bindings):
        _ = context, bindings
        calls.append(node["id"])
        return HandlerResult(True, output(node["id"]))

    result = await execute_workflow(
        compile_source(source, tmp_path),
        handlers=NodeHandlerRegistry({"raticode.bash_command": fake_handler}),
    )

    assert result.outcome == "pass"
    assert set(calls) == {"decide", "always", "fallback"}


@pytest.mark.anyio
async def test_allowed_failure_cannot_reuse_old_output_for_structured_route(
    tmp_path: Path,
) -> None:
    source = """Rattish: 1

Workflow:
  name: Failure route output eligibility

Node flaky:
  type: bash-command
  command: flaky
  allow-fail: true
  to:
    - flaky when succeeded
    - observed when node.flaky.output["exit_code"] == 0
    - recovery when failed

Node observed:
  type: bash-command
  command: observed
  needs: flaky

Node recovery:
  type: bash-command
  command: recovery
  needs: flaky
"""
    calls: Counter[str] = Counter()

    async def fake_handler(node, context, bindings):
        _ = context, bindings
        node_id = node["id"]
        calls[node_id] += 1
        if node_id == "flaky" and calls[node_id] == 2:
            await anyio.sleep(0.02)
            return HandlerResult(
                False,
                output(),
                RuntimeErrorInfo("command", "TEST_ALLOWED", "allowed"),
            )
        return HandlerResult(True, output(node_id))

    result = await execute_workflow(
        compile_source(source, tmp_path),
        handlers=NodeHandlerRegistry({"raticode.bash_command": fake_handler}),
    )

    assert result.outcome == "pass"
    assert calls == {"flaky": 2, "observed": 1, "recovery": 1}


@pytest.mark.anyio
async def test_explicit_max_concurrency_allows_different_groups_to_overlap(
    tmp_path: Path,
) -> None:
    source = """Rattish: 1

Workflow:
  name: Concurrent target

Node a:
  type: bash-command
  command: a
  to: target

Node c:
  type: bash-command
  command: c
  to: target

Node target:
  type: bash-command
  command: target
  needs: c
  max-concurrency: 2
"""
    active = 0
    peak_active = 0
    first_target_started = anyio.Event()
    second_target_started = anyio.Event()

    async def fake_handler(node, context, bindings):
        nonlocal active, peak_active
        _ = context, bindings
        if node["id"] == "a":
            await first_target_started.wait()
        if node["id"] == "target":
            active += 1
            peak_active = max(peak_active, active)
            if active == 1:
                first_target_started.set()
                await second_target_started.wait()
            else:
                second_target_started.set()
            active -= 1
        return HandlerResult(True, output(node["id"]))

    result = await execute_workflow(
        compile_source(source, tmp_path),
        handlers=NodeHandlerRegistry({"raticode.bash_command": fake_handler}),
    )

    assert result.outcome == "pass"
    assert peak_active == 2
    assert sum(record.node_id == "target" for record in result.runs) == 2
