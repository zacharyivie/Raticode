"""Deterministic routed execution for compiled Rattish workflows."""

from __future__ import annotations

import heapq
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import anyio

from gofer.core.approvals import NotificationAdapter
from gofer.core.provider_capabilities import provider_app_version
from gofer.core.provider_profiles import ProviderProfile, load_provider_profiles
from gofer.core.usage_context import observe_usage
from gofer.rattish import agent_sandbox
from gofer.rattish.ir_validation import validate_ir_invariants
from gofer.rattish.provider_fallback import (
    choice_summary,
    fallback_reason,
    provider_choices,
    sign_in,
    skip_record,
    skips_account,
    switch_message,
)
from gofer.rattish.provider_runtime import default_provider_subscriptions, runtime_subscription_id
from gofer.rattish.result_cache import ResultCache
from gofer.rattish.run_usage import RunUsageMeter
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
    # The run this result was copied from when a resumed run reused it.
    reused_from: str | None = None
    # Tokens and cost of the provider calls this activation made, when it made any.
    usage: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class WorkflowExecutionResult:
    outcome: Literal["pass", "failure"]
    runs: tuple[NodeRunRecord, ...]
    outputs: dict[str, Any]
    latest_node_outputs: dict[str, Any]
    error: RuntimeErrorInfo | None = None


NodeEventCallback = Callable[[dict[str, Any], NodeRunRecord | None], None]


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
        on_node_event: NodeEventCallback | None,
        replay: Sequence[Mapping[str, Any]] = (),
        replay_source: str | None = None,
        usage: RunUsageMeter | None = None,
        result_cache: ResultCache | None = None,
    ) -> None:
        self.ir = ir
        self.result_cache = result_cache
        self.nodes = {node["id"]: node for node in ir["nodes"]}
        self.workflow_inputs = workflow_inputs
        self.trigger_events = trigger_events
        self.handlers = handlers
        self.trash_root = trash_root
        self.subscriptions = subscriptions
        self.data_dir = data_dir
        self.notification_adapter = notification_adapter
        self.on_node_event = on_node_event
        self.agent_run_memory: dict[str, list[dict[str, str]]] = {}
        self.lineage_id = "root"
        self.run_id = uuid.uuid4().hex
        self._usage = usage
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
        # Resume replays an earlier run's records in their saved order. The k-th
        # start of a node in one activation group and lineage is that key's k-th
        # record, so loops, joins and cycles rebuild the same scheduler state.
        # Finished results are reused. Other work is held until nothing more can
        # be replayed, so live results can't reorder the replay. Later starts may
        # still reuse records (a loop can finish iterations after one failed), but
        # never in a lineage where live work has completed, since its results may
        # differ from the earlier run's.
        self.replay_source = replay_source
        self.replaying = bool(replay)
        self.dirty_lineages: set[str] = set()
        self.replay_records: dict[tuple[str, str, str], list[tuple[int, Mapping[str, Any]]]] = {}
        for index, prior in enumerate(replay):
            key = (prior["node_id"], prior["activation_group_id"], prior["activation_lineage_id"])
            self.replay_records.setdefault(key, []).append((index, prior))
        self.replay_starts: dict[tuple[str, str, str], int] = {}
        self.replay_ready: list[tuple[int, str, _Activation, int, Mapping[str, Any]]] = []
        self.replay_held: list[
            tuple[str, _Activation, int, dict[str, Any], dict[str, str], dict[str, Any]]
        ] = []
        self.reused = 0
        self.reusable = sum(1 for prior in replay if prior["outcome"] in _REUSABLE)

    @property
    def usage(self) -> RunUsageMeter:
        if self._usage is None:
            self._usage = RunUsageMeter.for_ir(self.ir, self.run_id)
        return self._usage

    def _check_usage_limit(self) -> None:
        """End the run with a budget error once a usage limit is crossed.

        It replaces a node's own failure, since crossing the limit is why the run ends.
        """
        reached = self.usage.limit_reached()
        if reached is not None:
            self.error = RuntimeErrorInfo(
                "budget", reached["code"], reached["message"], reached["details"]
            )

    async def run(self) -> WorkflowExecutionResult:
        self.usage  # noqa: B018 - created before any node runs, inside the caller's context
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
                if self.replay_ready:
                    self._replay_next()
                    # Let a stop request cancel a long replay.
                    await anyio.sleep(0)
                    if self.error is None:
                        self._start_runnable(task_group, send)
                    continue
                if self.replaying:
                    self._finish_replay(task_group, send)
                if not self._running_count():
                    self._start_runnable(task_group, send)
                    if self.error is not None or not self._running_count():
                        break
                completion = await receive.receive()
                self._process_completion(completion)
                self._check_usage_limit()
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
                arguments = (
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
                )
                if self._replayed(node_id, activation, self.run_counts[node_id]):
                    pass
                elif self.replaying:
                    self.replay_held.append(arguments)
                else:
                    task_group.start_soon(self._run_activation, *arguments, send)
                made_progress = True

    def _replayed(self, node_id: str, activation: _Activation, run_number: int) -> bool:
        """Queue a saved result for this start, if the earlier run has one to reuse."""
        if self.replay_source is None or self.reused >= self.reusable:
            return False
        key = (node_id, activation.group_id, activation.lineage_id)
        occurrence = self.replay_starts.get(key, 0)
        self.replay_starts[key] = occurrence + 1
        prior = self.replay_records.get(key, [])
        if (
            activation.lineage_id in self.dirty_lineages
            or occurrence >= len(prior)
            or prior[occurrence][1]["outcome"] not in _REUSABLE
        ):
            return False
        index, record = prior[occurrence]
        heapq.heappush(self.replay_ready, (index, node_id, activation, run_number, record))
        return True

    def _replay_next(self) -> None:
        _, node_id, activation, run_number, prior = heapq.heappop(self.replay_ready)
        error = prior.get("error")
        result = NodeExecutionResult(
            node_id,
            prior["outcome"],
            prior["output"],
            RuntimeErrorInfo(
                error["kind"], error["code"], error["message"], dict(error.get("details") or {})
            )
            if error
            else None,
            provider=prior.get("provider"),
            fallbacks=tuple(prior.get("fallbacks") or ()),
            cached_at=prior.get("cached_at"),
        )
        self.reused += 1
        self._process_completion(
            _Completion(
                activation,
                NodeRunRecord(
                    node_id,
                    run_number,
                    activation.lineage_id,
                    activation.group_id,
                    prior["started_at"],
                    prior["finished_at"],
                    prior["duration_ms"],
                    result,
                    reused_from=self.replay_source,
                ),
            )
        )

    def _finish_replay(
        self, task_group: anyio.abc.TaskGroup, send: anyio.abc.ObjectSendStream[_Completion]
    ) -> None:
        """Start the work replay could not reuse. Nothing is reused after this."""
        self.replaying = False
        held, self.replay_held = self.replay_held, []
        for arguments in held:
            task_group.start_soon(self._run_activation, *arguments, send)

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
        try:
            result = await self._run_attempts(
                node, activation, run_number, node_outputs, node_statuses, node_errors
            )
        except anyio.get_cancelled_exc_class():
            self._emit_node_event("node_stopped", node_id, activation, run_number)
            raise
        finished_at = datetime.now(UTC).isoformat()
        await send.send(
            _Completion(
                activation,
                NodeRunRecord(
                    node_id,
                    run_number,
                    activation.lineage_id,
                    activation.group_id,
                    started_at,
                    finished_at,
                    round((time.monotonic() - started_clock) * 1000),
                    result,
                    usage=self.usage.activation_usage(node_id, run_number),
                ),
            )
        )

    async def _run_attempts(
        self,
        node: Mapping[str, Any],
        activation: _Activation,
        run_number: int,
        node_outputs: Mapping[str, Any],
        node_statuses: Mapping[str, str],
        node_errors: Mapping[str, Mapping[str, Any]],
    ) -> NodeExecutionResult:
        """Run a node's attempts, moving down its fallback providers when it has any.

        Each provider gets the node's full retry-count. The next fallback entry is used
        only when the current one's last attempt failed with a provider availability
        error and the run's usage limits still allow more calls. After a sign-in hits a
        rate limit, later entries that are another plan sign-in of the same provider
        are skipped and recorded as skipped.
        """
        choices = provider_choices(node)
        if len(choices) < 2:
            version = await _app_version(choices[0]) if choices else None
            result, _ = await self._run_retries(
                node, activation, run_number, node_outputs, node_statuses, node_errors, None, 0
            )
            return (
                replace(result, provider=_used_provider(choices[0], version, result))
                if choices
                else result
            )
        fallbacks: list[dict[str, Any]] = []
        # Each provider's sign-ins that hit a usage limit in this step.
        limited: dict[str, set[str]] = {}
        profiles: dict[str, ProviderProfile] | None = None
        attempts_made = 0
        index = 0
        while True:
            choice = choices[index]
            version = await _app_version(choice)
            result, used = await self._run_retries(
                node,
                activation,
                run_number,
                node_outputs,
                node_statuses,
                node_errors,
                choice if index else None,
                attempts_made,
            )
            attempts_made += used
            reason = fallback_reason(result.error) if result.outcome != "success" else None
            if reason is None or index + 1 == len(choices) or self.usage.stop_requested():
                return replace(
                    result,
                    provider=_used_provider(choice, version, result),
                    fallbacks=tuple(fallbacks),
                )
            if reason == "rate_limit" or limited:
                if profiles is None:
                    profiles = self._provider_profiles()
                if reason == "rate_limit":
                    limited.setdefault(choice["provider_id"], set()).add(
                        sign_in(choice, profiles)[0]
                    )
                while index + 1 < len(choices) and skips_account(
                    choices[index + 1], profiles, limited
                ):
                    index += 1
                    fallbacks.append(skip_record(choice, choices[index]))
                if index + 1 == len(choices):
                    return replace(
                        result,
                        provider=_used_provider(choice, version, result),
                        fallbacks=tuple(fallbacks),
                    )
            index += 1
            switch = {
                "from": choice_summary(choice),
                "to": choice_summary(choices[index]),
                "reason": reason,
                "message": result.error.message if result.error is not None else "",
            }
            fallbacks.append(switch)
            self._emit_node_event(
                "node_fell_back",
                node["id"],
                activation,
                run_number,
                attempt=attempts_made,
                message=switch_message(switch),
                provider=switch["to"],
                from_provider=switch["from"],
                fallback_reason=reason,
            )

    def _provider_profiles(self) -> dict[str, ProviderProfile]:
        """The saved provider profiles, or none when the store can't be read.

        With none, every named profile counts as a sign-in of its own, so a step still
        never moves to another account at a usage limit.
        """
        try:
            return load_provider_profiles(self.data_dir)
        except (OSError, ValueError):
            return {}

    async def _run_retries(
        self,
        node: Mapping[str, Any],
        activation: _Activation,
        run_number: int,
        node_outputs: Mapping[str, Any],
        node_statuses: Mapping[str, str],
        node_errors: Mapping[str, Mapping[str, Any]],
        provider_choice: Mapping[str, Any] | None,
        attempts_before: int,
    ) -> tuple[NodeExecutionResult, int]:
        """Run one provider's attempts: the first try plus the node's retry-count.

        Returns the last result and how many attempts ran.
        """
        started: dict[str, Any] = (
            {"provider": choice_summary(provider_choice)} if provider_choice is not None else {}
        )
        for attempt in range(node["execution"]["retry_count"] + 1):
            self._emit_node_event(
                "node_started",
                node["id"],
                activation,
                run_number,
                attempt=attempts_before + attempt + 1,
                **started,
            )
            with observe_usage(self.usage.observer(node["id"], run_number)):
                result = await self._execute_node(
                    node, node_outputs, node_statuses, node_errors, provider_choice
                )
            if result.cached_at is not None:
                # No provider call was made: the activation records 0 tokens and $0.
                self.usage.add_cached(node["id"], run_number)
            if result.outcome == "success" or attempt == node["execution"]["retry_count"]:
                return result, attempt + 1
            details = result.error.details if result.error is not None else {}
            # A handler marks errors another attempt can't fix, such as a rejected key.
            if details.get("retryable") is False:
                return result, attempt + 1
            # Another attempt would make more calls after a usage limit was crossed.
            if self.usage.stop_requested():
                return result, attempt + 1
            self._emit_node_event(
                "node_retried",
                node["id"],
                activation,
                run_number,
                attempt=attempts_before + attempt + 1,
                message=result.error.message if result.error else "Node failed; retrying.",
            )
            delay_ms = node["execution"]["retry_delay_ms"]
            # A rate limit names how long to wait; waiting less would only fail again.
            retry_after_ms = details.get("retry_after_ms")
            if isinstance(retry_after_ms, int) and retry_after_ms > delay_ms:
                delay_ms = retry_after_ms
            if delay_ms:
                await anyio.sleep(delay_ms / 1000)
        raise AssertionError("An activation must execute at least one attempt.")

    async def _execute_node(
        self,
        node: Mapping[str, Any],
        node_outputs: Mapping[str, Any],
        node_statuses: Mapping[str, str],
        node_errors: Mapping[str, Mapping[str, Any]],
        provider_choice: Mapping[str, Any] | None = None,
    ) -> NodeExecutionResult:
        return await execute_node(
            self.ir,
            node["id"],
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
            provider_choice=provider_choice,
            result_cache=self.result_cache,
        )

    def _emit_node_event(
        self,
        event_type: str,
        node_id: str,
        activation: _Activation,
        run_number: int,
        record: NodeRunRecord | None = None,
        **details: Any,
    ) -> None:
        if self.on_node_event is not None:
            self.on_node_event(
                {
                    "type": event_type,
                    "at": record.finished_at if record else datetime.now(UTC).isoformat(),
                    "node_id": node_id,
                    "run_number": run_number,
                    "activation_lineage_id": activation.lineage_id,
                    "activation_group_id": activation.group_id,
                    **details,
                },
                record,
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
        if self.replay_source is not None and record.reused_from is None:
            self.dirty_lineages.add(lineage_id)
        self._emit_node_event(
            "node_completed",
            node_id,
            completion.activation,
            record.run_number,
            record,
            outcome=result.outcome,
            message=result.error.message if result.error else "",
            **({"reused": True} if record.reused_from else {}),
            **({"cached": True} if result.cached_at is not None else {}),
        )
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
            if state.parent_lineage_id in self.dirty_lineages:
                self.dirty_lineages.add(group_id)
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


_REUSABLE = frozenset({"success", "allowed_failure"})


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
    on_node_event: NodeEventCallback | None = None,
    replay: Sequence[Mapping[str, Any]] = (),
    replay_source: str | None = None,
    usage_meter: RunUsageMeter | None = None,
    result_cache: ResultCache | None = None,
) -> WorkflowExecutionResult:
    """Execute one validated Rattish IR workflow using routed activation semantics.

    ``replay`` holds an earlier run's saved node records, in their saved order,
    for a resumed run; ``replay_source`` is that run's ID. ``usage_meter`` counts the
    run's provider calls and enforces its usage limits; one is made from the IR when
    it is omitted. ``result_cache`` lets nodes with ``cache: DURATION`` reuse saved
    answers; without it (as in test sets) every node runs.
    """
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
        on_node_event,
        replay,
        replay_source,
        usage_meter,
        result_cache,
    )
    if run_id is not None:
        scheduler.run_id = run_id
    try:
        return await _run_scheduler(scheduler, ir["workflow"]["timeout_ms"])
    finally:
        # Sandboxed agent steps keep temp files and caches here for the run.
        agent_sandbox.remove_run_scratch(scheduler.data_dir, scheduler.run_id)


async def _run_scheduler(
    scheduler: _WorkflowScheduler, timeout_ms: int | None
) -> WorkflowExecutionResult:
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


async def _app_version(choice: Mapping[str, Any]) -> str | None:
    """The installed version of the provider app a step is about to start, if it has one.

    Plain API providers have no app, so they have no version.
    """
    try:
        subscription = runtime_subscription_id(str(choice["provider_id"]))
    except ValueError:
        return None
    return await anyio.to_thread.run_sync(provider_app_version, subscription)


def _used_provider(
    choice: Mapping[str, Any], version: str | None, result: NodeExecutionResult
) -> dict[str, Any]:
    """The provider a step used for its record, with the app version when one started.

    A cached answer started no provider app, so it records no version.
    """
    summary = choice_summary(choice)
    if version and result.cached_at is None:
        summary["provider_version"] = version
    return summary
