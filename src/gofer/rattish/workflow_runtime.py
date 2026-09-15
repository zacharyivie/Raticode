"""Deterministic routed execution for compiled Rattish workflows."""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import anyio

from gofer.core.approvals import NotificationAdapter
from gofer.rattish.ir_validation import validate_ir_invariants
from gofer.rattish.provider_runtime import default_provider_subscriptions
from gofer.rattish.runtime import (
    _MISSING,
    DEFAULT_NODE_HANDLERS,
    NodeExecutionResult,
    NodeHandlerRegistry,
    RuntimeErrorInfo,
    _error_document,
    _evaluate_predicate,
    _predicate_uses_output,
    _prepare_workflow_inputs,
    _reference_value,
    _require_validated_ir,
    execute_node,
)
from gofer.rattish.schema_compat import instance_matches_schema
from gofer.subscriptions.base import Subscription
from gofer.utils.paths import get_data_dir


@dataclass(frozen=True, slots=True)
class NodeRunRecord:
    node_id: str
    run_number: int
    activation_lineage_id: str
    activation_group_id: str
    started_at: str
    finished_at: str
    duration_ms: int
    result: NodeExecutionResult


@dataclass(frozen=True, slots=True)
class WorkflowExecutionResult:
    outcome: Literal["pass", "failure"]
    runs: tuple[NodeRunRecord, ...]
    outputs: dict[str, Any]
    latest_node_outputs: dict[str, Any]
    error: RuntimeErrorInfo | None = None


@dataclass(frozen=True, slots=True)
class _Activation:
    group_id: str
    sequence: int
    lineage_id: str
    scoped_outputs: Mapping[str, Any]
    loop_lineages: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class _Completion:
    activation: _Activation
    record: NodeRunRecord


@dataclass(slots=True)
class _LoopState:
    node_id: str
    lineage_id: str
    items: list[Mapping[str, Any]]
    infinite: bool
    max_concurrency: int
    fail_fast: bool
    routes: list[Mapping[str, Any]]
    parent_outputs: Mapping[str, Any]
    parent_lineages: Mapping[str, str]
    parent_lineage_id: str
    next_index: int = 0
    closed: bool = False
    active_groups: set[str] = field(default_factory=set)


class _WorkflowScheduler:
    def __init__(
        self,
        ir: Mapping[str, Any],
        workflow_inputs: Mapping[str, Any],
        trigger_events: tuple[Mapping[str, Any], ...],
        handlers: NodeHandlerRegistry,
        trash_root: Path | None,
        subscriptions: Mapping[str, Subscription],
        data_dir: Path,
        notification_adapter: NotificationAdapter | None,
    ) -> None:
        self.ir = ir
        self.nodes = {node["id"]: node for node in ir["nodes"]}
        self.workflow_inputs = workflow_inputs
        self.trigger_events = trigger_events
        self.handlers = handlers
        self.trash_root = trash_root
        self.subscriptions = subscriptions
        self.data_dir = data_dir
        self.notification_adapter = notification_adapter
        self.agent_run_memory: dict[str, list[dict[str, str]]] = {}
        self.lineage_id = "root"
        self.run_id = uuid.uuid4().hex
        self.latest_outputs: dict[str, Any] = {}
        self.latest_statuses: dict[str, str] = {}
        self.latest_errors: dict[str, dict[str, Any]] = {}
        self.satisfied_by_lineage: dict[str, set[str]] = {self.lineage_id: set()}
        self.outputs_by_lineage: dict[str, dict[str, Any]] = {self.lineage_id: {}}
        self.statuses_by_lineage: dict[str, dict[str, str]] = {self.lineage_id: {}}
        self.errors_by_lineage: dict[str, dict[str, dict[str, Any]]] = {self.lineage_id: {}}
        self.pending: dict[str, OrderedDict[str, _Activation]] = {
            node_id: OrderedDict() for node_id in self.nodes
        }
        self.running_groups: dict[str, set[str]] = {node_id: set() for node_id in self.nodes}
        self.run_counts: dict[str, int] = {node_id: 0 for node_id in self.nodes}
        self.total_runs = 0
        self.sequence = 0
        self.records: list[NodeRunRecord] = []
        self.error: RuntimeErrorInfo | None = None
        self.loops: dict[str, _LoopState] = {}
        self.deferred_loop_failures: list[RuntimeErrorInfo] = []

    async def run(self) -> WorkflowExecutionResult:
        completion_capacity = sum(
            node["execution"]["max_concurrency"] for node in self.nodes.values()
        )
        send, receive = anyio.create_memory_object_stream[_Completion](
            max_buffer_size=max(1, completion_capacity)
        )
        async with send, receive, anyio.create_task_group() as task_group:
            for node_id in sorted(self.nodes):
                if self.nodes[node_id]["execution"]["initial_activation"]:
                    self._enqueue(node_id, f"initial:{node_id}")
            self._start_runnable(task_group, send)

            while self._running_count() or self._has_runnable_pending():
                if self.error is not None:
                    task_group.cancel_scope.cancel()
                    break
                if not self._running_count():
                    self._start_runnable(task_group, send)
                    if self.error is not None or not self._running_count():
                        break
                completion = await receive.receive()
                self._process_completion(completion)
                if self.error is not None:
                    task_group.cancel_scope.cancel()
                    break
                self._start_runnable(task_group, send)

        if self.error is None and self.deferred_loop_failures:
            self.error = self.deferred_loop_failures[0]
        if self.error is None:
            self.error = self._unresolved_join_error()
        outputs: dict[str, Any] = {}
        if self.error is None:
            outputs, self.error = self._workflow_outputs()
        outcome: Literal["pass", "failure"] = "failure" if self.error else "pass"
        return WorkflowExecutionResult(
            outcome,
            tuple(self.records),
            outputs if self.error is None else {},
            dict(self.latest_outputs),
            self.error,
        )

    def _enqueue(
        self,
        node_id: str,
        group_id: str,
        *,
        lineage_id: str | None = None,
        scoped_outputs: Mapping[str, Any] | None = None,
        loop_lineages: Mapping[str, str] | None = None,
    ) -> None:
        if group_id in self.running_groups[node_id] or group_id in self.pending[node_id]:
            return
        self.sequence += 1
        self.pending[node_id][group_id] = _Activation(
            group_id,
            self.sequence,
            lineage_id or self.lineage_id,
            dict(scoped_outputs or {}),
            dict(loop_lineages or {}),
        )

    def _start_runnable(
        self,
        task_group: anyio.abc.TaskGroup,
        send: anyio.abc.ObjectSendStream[_Completion],
    ) -> None:
        made_progress = True
        while made_progress and self.error is None:
            made_progress = False
            candidates: list[tuple[int, str, _Activation]] = []
            for node_id, activations in self.pending.items():
                capacity = self.nodes[node_id]["execution"]["max_concurrency"] - len(
                    self.running_groups[node_id]
                )
                runnable = [
                    activation
                    for activation in activations.values()
                    if self._is_unlocked(node_id, activation)
                ]
                for activation in runnable[:capacity]:
                    candidates.append((activation.sequence, node_id, activation))
            for _, node_id, activation in sorted(candidates):
                if activation.group_id not in self.pending[node_id]:
                    continue
                if not self._reserve_run(node_id):
                    return
                self.pending[node_id].pop(activation.group_id)
                self.running_groups[node_id].add(activation.group_id)
                task_group.start_soon(
                    self._run_activation,
                    node_id,
                    activation,
                    self.run_counts[node_id],
                    {
                        **self.outputs_by_lineage.get(activation.lineage_id, {}),
                        **activation.scoped_outputs,
                    },
                    {
                        **self.statuses_by_lineage.get(activation.lineage_id, {}),
                    },
                    {
                        **self.errors_by_lineage.get(activation.lineage_id, {}),
                    },
                    send,
                )
                made_progress = True

    async def _run_activation(
        self,
        node_id: str,
        activation: _Activation,
        run_number: int,
        node_outputs: Mapping[str, Any],
        node_statuses: Mapping[str, str],
        node_errors: Mapping[str, Mapping[str, Any]],
        send: anyio.abc.ObjectSendStream[_Completion],
    ) -> None:
        node = self.nodes[node_id]
        started_at = datetime.now(UTC).isoformat()
        started_clock = time.monotonic()
        result: NodeExecutionResult | None = None
        for attempt in range(node["execution"]["retry_count"] + 1):
            result = await execute_node(
                self.ir,
                node_id,
                workflow_inputs=self.workflow_inputs,
                trigger_events=self.trigger_events,
                node_outputs=node_outputs,
                node_statuses=node_statuses,
                node_errors=node_errors,
                handlers=self.handlers,
                trash_root=self.trash_root,
                subscriptions=self.subscriptions,
                data_dir=self.data_dir,
                agent_run_memory=self.agent_run_memory,
                notification_adapter=self.notification_adapter,
                run_id=self.run_id,
            )
            if result.outcome == "success" or attempt == node["execution"]["retry_count"]:
                break
            delay_ms = node["execution"]["retry_delay_ms"]
            if delay_ms:
                await anyio.sleep(delay_ms / 1000)
        assert result is not None
        finished_at = datetime.now(UTC).isoformat()
        await send.send(
            _Completion(
                activation,
                NodeRunRecord(
                    node_id,
                    run_number,
                    (activation.lineage_id),
                    activation.group_id,
                    started_at,
                    finished_at,
                    round((time.monotonic() - started_clock) * 1000),
                    result,
                ),
            )
        )

    def _process_completion(self, completion: _Completion) -> None:
        record = completion.record
        node_id = record.node_id
        result = record.result
        lineage_id = completion.activation.lineage_id
        lineage_outputs = self.outputs_by_lineage.setdefault(lineage_id, {})
        lineage_statuses = self.statuses_by_lineage.setdefault(lineage_id, {})
        lineage_errors = self.errors_by_lineage.setdefault(lineage_id, {})
        self.running_groups[node_id].discard(completion.activation.group_id)
        self.records.append(record)
        if result.outcome == "success":
            self.latest_outputs[node_id] = result.output
            self.latest_statuses[node_id] = "success"
            self.latest_errors.pop(node_id, None)
            lineage_outputs[node_id] = result.output
            lineage_statuses[node_id] = "success"
            lineage_errors.pop(node_id, None)
        else:
            self.latest_statuses[node_id] = "failure"
            lineage_statuses[node_id] = "failure"
            if result.error is not None:
                self.latest_errors[node_id] = _error_document(result.error)
                lineage_errors[node_id] = _error_document(result.error)
        if result.outcome == "failure":
            if self._defer_loop_failure(completion.activation, result.error):
                self._settle_loop_groups()
                return
            self.error = result.error or RuntimeErrorInfo(
                "internal", "RATTISH_NODE_FAILED", f"Node {node_id!r} failed."
            )
            return

        is_loop = self.nodes[node_id]["type"] == "loop"
        is_break = self.nodes[node_id]["type"] == "break"
        if is_loop:
            self.satisfied_by_lineage.setdefault(lineage_id, set()).add(node_id)
            self._open_loop(completion)
            self._settle_loop_groups()
        if is_break:
            self._close_loop(completion.activation, self.nodes[node_id])
            self._settle_loop_groups()
        if self.nodes[node_id]["execution"]["finish"] == "fail":
            self.error = RuntimeErrorInfo(
                "workflow_control",
                "RATTISH_FINISH_FAIL",
                f"Failure terminal {node_id!r} completed.",
                {"node_id": node_id},
            )
            return
        if is_loop and self.nodes[node_id]["execution"]["finish"] is None:
            return

        satisfied = self.satisfied_by_lineage.setdefault(lineage_id, set())
        locked_before = {
            target: not set(self.nodes[target]["readiness"]["needs"]) <= satisfied
            for target in self.nodes
        }
        satisfied.add(node_id)
        for route in self._matching_routes(self.nodes[node_id], result, lineage_id):
            self._enqueue(
                route["target"],
                completion.activation.group_id,
                lineage_id=lineage_id,
                scoped_outputs=completion.activation.scoped_outputs,
                loop_lineages=completion.activation.loop_lineages,
            )
        for target in sorted(self.nodes):
            if locked_before[target] and self._lineage_is_unlocked(target, lineage_id):
                self._merge_locked_signals(target, lineage_id)
        self._settle_loop_groups()

    def _open_loop(self, completion: _Completion) -> None:
        output = completion.record.result.output
        if not isinstance(output, Mapping):
            raise ValueError("Loop handler returned an invalid control output.")
        node_id = completion.record.node_id
        lineage_id = (
            f"{completion.activation.group_id}:loop:{node_id}:{completion.record.run_number}"
        )
        state = _LoopState(
            node_id=node_id,
            lineage_id=lineage_id,
            items=[item for item in output.get("items", []) if isinstance(item, Mapping)],
            infinite=bool(output.get("infinite", False)),
            max_concurrency=int(output.get("max_concurrency", 1)),
            fail_fast=bool(output.get("fail_fast", False)),
            routes=self._matching_routes(
                self.nodes[node_id], completion.record.result, completion.activation.lineage_id
            ),
            parent_outputs=completion.activation.scoped_outputs,
            parent_lineages=completion.activation.loop_lineages,
            parent_lineage_id=completion.activation.lineage_id,
        )
        self.loops[lineage_id] = state
        self._fill_loop_capacity(state)

    def _fill_loop_capacity(self, state: _LoopState) -> None:
        while not state.closed and len(state.active_groups) < state.max_concurrency:
            if not state.infinite and state.next_index >= len(state.items):
                break
            index = state.next_index
            state.next_index += 1
            item = {"index": index} if state.infinite else dict(state.items[index])
            item.setdefault("index", index)
            group_id = f"{state.lineage_id}:iteration:{index}"
            state.active_groups.add(group_id)
            scoped_outputs = {**state.parent_outputs, state.node_id: item}
            loop_lineages = {**state.parent_lineages, state.node_id: state.lineage_id}
            self.satisfied_by_lineage[group_id] = {
                *self.satisfied_by_lineage.get(state.parent_lineage_id, set()),
                state.node_id,
            }
            self.outputs_by_lineage[group_id] = {
                **self.outputs_by_lineage.get(state.parent_lineage_id, {}),
                state.node_id: item,
            }
            self.statuses_by_lineage[group_id] = dict(
                self.statuses_by_lineage.get(state.parent_lineage_id, {})
            )
            self.errors_by_lineage[group_id] = dict(
                self.errors_by_lineage.get(state.parent_lineage_id, {})
            )
            for route in state.routes:
                self._enqueue(
                    route["target"],
                    group_id,
                    lineage_id=group_id,
                    scoped_outputs=scoped_outputs,
                    loop_lineages=loop_lineages,
                )
            if not state.routes:
                state.active_groups.discard(group_id)

    def _settle_loop_groups(self) -> None:
        pending_groups = {
            activation.group_id
            for activations in self.pending.values()
            for activation in activations.values()
        }
        running_groups = {group for groups in self.running_groups.values() for group in groups}
        live_groups = pending_groups | running_groups
        for state in list(self.loops.values()):
            state.active_groups = {
                group
                for group in state.active_groups
                if any(live == group or live.startswith(f"{group}:") for live in live_groups)
            }
            self._fill_loop_capacity(state)

    def _close_loop(self, activation: _Activation, node: Mapping[str, Any]) -> None:
        loop_id = node["control"]["loop_node_id"]
        lineage_id = activation.loop_lineages.get(loop_id)
        if lineage_id is None:
            self.error = RuntimeErrorInfo(
                "workflow_control",
                "RATTISH_BREAK_OUTSIDE_LOOP",
                f"Break node {node['id']!r} ran outside loop {loop_id!r}.",
            )
            return
        state = self.loops.get(lineage_id)
        if state is None or state.closed:
            return
        state.closed = True
        for node_id, activations in self.pending.items():
            for group_id, queued in list(activations.items()):
                if queued.loop_lineages.get(loop_id) == lineage_id:
                    activations.pop(group_id)

    def _defer_loop_failure(self, activation: _Activation, error: RuntimeErrorInfo | None) -> bool:
        for lineage_id in activation.loop_lineages.values():
            state = self.loops.get(lineage_id)
            if state is not None and not state.fail_fast:
                self.deferred_loop_failures.append(
                    error
                    or RuntimeErrorInfo(
                        "internal",
                        "RATTISH_NODE_FAILED",
                        "A loop iteration branch failed.",
                        {"loop": state.node_id, "lineage": lineage_id},
                    )
                )
                return True
        return False

    def _matching_routes(
        self, node: Mapping[str, Any], result: NodeExecutionResult, lineage_id: str
    ) -> list[Mapping[str, Any]]:
        routes = node["routes"]
        conditionals = [route for route in routes if route["mode"] == "conditional"]
        matched_conditionals = [
            route
            for route in conditionals
            if result.outcome in route["eligible_outcomes"]
            and not (
                result.outcome == "allowed_failure" and _predicate_uses_output(route["predicate"])
            )
            and _evaluate_predicate(
                route["predicate"],
                result,
                self.workflow_inputs,
                self.trigger_events,
                self.outputs_by_lineage.get(lineage_id, {}),
                self.statuses_by_lineage.get(lineage_id, {}),
                self.errors_by_lineage.get(lineage_id, {}),
            )
        ]
        matched = [
            route
            for route in routes
            if route["mode"] == "unconditional" and result.outcome in route["eligible_outcomes"]
        ]
        matched.extend(matched_conditionals)
        if not matched_conditionals:
            matched.extend(
                route
                for route in routes
                if route["mode"] == "otherwise" and result.outcome in route["eligible_outcomes"]
            )
        return matched

    def _merge_locked_signals(self, node_id: str, lineage_id: str) -> None:
        activations = [
            activation
            for activation in self.pending[node_id].values()
            if activation.lineage_id == lineage_id
        ]
        if len(activations) <= 1:
            return
        group_id = "merged:" + "+".join(sorted(item.group_id for item in activations))
        sequence = min(item.sequence for item in activations)
        for activation in activations:
            self.pending[node_id].pop(activation.group_id)
        representative = activations[0]
        self.pending[node_id][group_id] = _Activation(
            group_id,
            sequence,
            lineage_id,
            representative.scoped_outputs,
            representative.loop_lineages,
        )

    def _reserve_run(self, node_id: str) -> bool:
        workflow_limit = self.ir["workflow"]["max_runs"]
        node_limit = self.nodes[node_id]["execution"]["max_runs"]
        if workflow_limit is not None and self.total_runs >= workflow_limit:
            self.error = RuntimeErrorInfo(
                "budget",
                "RATTISH_RUN_LIMIT_EXCEEDED",
                "The workflow max-runs limit was reached.",
                {"scope": "workflow", "max_runs": workflow_limit, "next_node": node_id},
            )
            return False
        if node_limit is not None and self.run_counts[node_id] >= node_limit:
            self.error = RuntimeErrorInfo(
                "budget",
                "RATTISH_RUN_LIMIT_EXCEEDED",
                f"Node {node_id!r} reached its max-runs limit.",
                {"scope": "node", "max_runs": node_limit, "node_id": node_id},
            )
            return False
        self.total_runs += 1
        self.run_counts[node_id] += 1
        return True

    def _lineage_is_unlocked(self, node_id: str, lineage_id: str) -> bool:
        return set(self.nodes[node_id]["readiness"]["needs"]) <= self.satisfied_by_lineage.get(
            lineage_id, set()
        )

    def _is_unlocked(self, node_id: str, activation: _Activation) -> bool:
        return self._lineage_is_unlocked(node_id, activation.lineage_id)

    def _running_count(self) -> int:
        return sum(len(groups) for groups in self.running_groups.values())

    def _has_runnable_pending(self) -> bool:
        return any(
            any(
                self._is_unlocked(node_id, activation)
                for activation in self.pending[node_id].values()
            )
            for node_id in self.nodes
        )

    def _unresolved_join_error(self) -> RuntimeErrorInfo | None:
        unresolved = []
        for node_id in sorted(self.nodes):
            needs = set(self.nodes[node_id]["readiness"]["needs"])
            for activation in self.pending[node_id].values():
                if self._is_unlocked(node_id, activation):
                    continue
                satisfied = self.satisfied_by_lineage.get(activation.lineage_id, set())
                statuses = self.statuses_by_lineage.get(activation.lineage_id, {})
                unresolved.append(
                    {
                        "node_id": node_id,
                        "activation_lineage_id": activation.lineage_id,
                        "received_predecessors": sorted(needs & satisfied),
                        "missing_predecessors": sorted(needs - satisfied),
                        "missing_states": {
                            name: statuses.get(name, "not_completed")
                            for name in sorted(needs - satisfied)
                        },
                    }
                )
        if not unresolved:
            return None
        return RuntimeErrorInfo(
            "workflow_control",
            "RATTISH_JOIN_UNRESOLVED",
            "The workflow reached quiescence with an unresolved join.",
            {"joins": unresolved},
        )

    def _workflow_outputs(self) -> tuple[dict[str, Any], RuntimeErrorInfo | None]:
        outputs: dict[str, Any] = {}
        for output in self.ir["workflow"]["outputs"]:
            value = _reference_value(
                output["source"],
                self.workflow_inputs,
                self.trigger_events,
                self.latest_outputs,
                self.latest_statuses,
                self.latest_errors,
            )
            if value is _MISSING:
                source = output["source"]
                return {}, RuntimeErrorInfo(
                    "output_validation",
                    "RATTISH_WORKFLOW_OUTPUT_MISSING",
                    f"Public workflow output {output['name']!r} has no value.",
                    {
                        "output": output["name"],
                        "root": source["root"],
                        "symbol": source["symbol"],
                        "channel": source["channel"],
                    },
                )
            if not instance_matches_schema(output["schema"], value):
                return {}, RuntimeErrorInfo(
                    "output_validation",
                    "RATTISH_WORKFLOW_OUTPUT_INVALID",
                    f"Public workflow output {output['name']!r} violates its schema.",
                    {"output": output["name"]},
                )
            outputs[output["name"]] = value
        return outputs, None


async def execute_workflow(
    ir: Mapping[str, Any],
    *,
    workflow_inputs: Mapping[str, Any] | None = None,
    trigger_events: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] | None = None,
    handlers: NodeHandlerRegistry | None = None,
    trash_root: Path | None = None,
    subscriptions: Mapping[str, Subscription] | None = None,
    data_dir: Path | None = None,
    notification_adapter: NotificationAdapter | None = None,
    run_id: str | None = None,
) -> WorkflowExecutionResult:
    """Execute one validated Rattish IR workflow using routed activation semantics."""
    _require_validated_ir(ir)
    validate_ir_invariants(ir)
    if not ir["nodes"]:
        return WorkflowExecutionResult(
            "failure",
            (),
            {},
            {},
            RuntimeErrorInfo(
                "workflow_control",
                "RATTISH_WORKFLOW_EMPTY",
                "The workflow has no nodes to execute.",
                {"workflow_id": ir["workflow"]["id"]},
            ),
        )
    prepared_inputs = _prepare_workflow_inputs(ir, workflow_inputs or {})
    scheduler = _WorkflowScheduler(
        ir,
        prepared_inputs,
        tuple(trigger_events or ()),
        handlers or DEFAULT_NODE_HANDLERS,
        trash_root,
        subscriptions if subscriptions is not None else default_provider_subscriptions(),
        data_dir or get_data_dir(),
        notification_adapter,
    )
    if run_id is not None:
        scheduler.run_id = run_id
    timeout_ms = ir["workflow"]["timeout_ms"]
    if timeout_ms is None:
        return await scheduler.run()
    try:
        with anyio.fail_after(timeout_ms / 1000):
            return await scheduler.run()
    except TimeoutError:
        return WorkflowExecutionResult(
            "failure",
            tuple(scheduler.records),
            {},
            dict(scheduler.latest_outputs),
            RuntimeErrorInfo(
                "timeout",
                "RATTISH_TIMEOUT",
                "The workflow active-processing timeout was reached.",
                {"scope": "workflow", "timeout_ms": timeout_ms},
            ),
        )
