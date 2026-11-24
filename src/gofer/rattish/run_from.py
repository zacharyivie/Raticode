"""Run from a node: reuse an earlier run's unchanged upstream results, run the rest.

A run from node N is a new run of the current source. Raticode picks an earlier
run (the newest one, or the one named) that reached N and in which every node
upstream of N is unchanged. Those nodes' saved results are replayed through the
resume replay path (see ``run_service``), then N and everything after it run from
the current source.

"Upstream" is every node N depends on, directly or through others: a ``needs``
entry, an incoming route, or a binding or route condition that reads another
node. Nodes that N also leads back to (a cycle through N) run again, since their
results could depend on N. A node's fingerprint covers its compiled definition
(without its position in the file), the definitions of everything it depends on,
and the workflow inputs any of those read. The earlier run's fingerprints come
from the compiled snapshot saved in its record.

Files a node reads while it runs, such as an agent's ``prompt-path`` file, are not
part of the source, so editing one doesn't count as a change.

An approval gate is never reused: someone decides it, and an earlier answer is not a
result to copy. It runs live with the same message and asks again, and every node
that depends on it runs again from the new answer. Nodes before it are still reused.

Version 1 refuses a node inside a loop body: one loop iteration can't be rerun on
its own.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from gofer.rattish.contracts import json_fingerprint
from gofer.rattish.run_values import saved_values_refusal

# Outcomes a replay can reuse, as in workflow_runtime.
_REUSABLE = frozenset({"success", "allowed_failure"})

# Nodes with this handler ask someone, so a run from a node never reuses their answer.
APPROVAL_GATE_HANDLER = "raticode.approval_gate"


class RunFromRefused(RuntimeError):
    """Raised with a sentence for the user when a run can't start from a node."""


@dataclass(frozen=True, slots=True)
class RunFromPlan:
    """What a run from ``from_node`` reuses."""

    from_node: str
    # The earlier run's record, as read.
    source: Mapping[str, Any]
    # Upstream nodes whose saved results are replayed, in compiled (IR) order.
    reused_nodes: tuple[str, ...]
    # The earlier run's node records for those nodes, in their saved order.
    replay: tuple[Mapping[str, Any], ...]
    # Approval gates the earlier run reached, before, beside or after ``from_node``.
    # None is reused, so each asks again when the run gets to it. In compiled (IR) order.
    ask_again: tuple[str, ...] = ()

    @property
    def source_run_id(self) -> str:
        return str(self.source["run_id"])


def _node_references(value: Any, found: set[str]) -> None:
    """Every node a compiled definition reads through a reference."""
    if isinstance(value, Mapping):
        if value.get("root") == "node" and isinstance(value.get("symbol"), str):
            found.add(value["symbol"])
        for item in value.values():
            _node_references(item, found)
    elif isinstance(value, list):
        for item in value:
            _node_references(item, found)


def _input_references(value: Any, found: set[str]) -> None:
    if isinstance(value, Mapping):
        if value.get("root") == "input" and isinstance(value.get("symbol"), str):
            found.add(value["symbol"])
        for item in value.values():
            _input_references(item, found)
    elif isinstance(value, list):
        for item in value:
            _input_references(item, found)


def node_dependencies(ir: Mapping[str, Any]) -> dict[str, set[str]]:
    """The nodes each node depends on directly."""
    nodes = {node["id"]: node for node in ir["nodes"]}
    dependencies: dict[str, set[str]] = {}
    for node_id, node in nodes.items():
        found: set[str] = set(node["readiness"]["needs"])
        found.update(node["readiness"]["incoming_route_sources"])
        _node_references({key: value for key, value in node.items() if key != "source_span"}, found)
        control = node.get("control")
        if isinstance(control, Mapping) and isinstance(control.get("loop_node_id"), str):
            found.add(control["loop_node_id"])
        dependencies[node_id] = {item for item in found if item in nodes and item != node_id}
    # Route sources are listed on the target, but a route condition reading a node
    # lives on the source; both are covered above.
    for node_id, node in nodes.items():
        for route in node["routes"]:
            target = route.get("target")
            if target in dependencies and target != node_id:
                dependencies[target].add(node_id)
    return dependencies


def _closure(start: Iterable[str], edges: Mapping[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    frontier = list(start)
    while frontier:
        current = frontier.pop()
        for item in edges.get(current, ()):
            if item not in seen:
                seen.add(item)
                frontier.append(item)
    return seen


def upstream_nodes(ir: Mapping[str, Any], node_id: str) -> set[str]:
    """Every node ``node_id`` depends on, directly or through others."""
    return _closure([node_id], node_dependencies(ir))


def _dependents(dependencies: Mapping[str, set[str]]) -> dict[str, set[str]]:
    dependents: dict[str, set[str]] = {}
    for target, sources in dependencies.items():
        for source in sources:
            dependents.setdefault(source, set()).add(target)
    return dependents


def downstream_nodes(ir: Mapping[str, Any], node_id: str) -> set[str]:
    """Every node that depends on ``node_id``, directly or through others."""
    return _closure([node_id], _dependents(node_dependencies(ir)))


def approval_gates(ir: Mapping[str, Any]) -> set[str]:
    """The approval gate nodes in a compiled source."""
    return {
        node["id"] for node in ir["nodes"] if node.get("runtime_handler") == APPROVAL_GATE_HANDLER
    }


def reused_nodes(ir: Mapping[str, Any], node_id: str) -> list[str]:
    """Nodes a run from ``node_id`` replays, in compiled (IR) order.

    Upstream nodes that ``node_id`` also leads back to are left out: they run again.
    So are approval gates and every node that depends on one: the gate asks again
    and those nodes run from its new answer.
    """
    dependencies = node_dependencies(ir)
    dependents = _dependents(dependencies)
    upstream = _closure([node_id], dependencies)
    reused = upstream - _closure([node_id], dependents) - {node_id}
    gates = reused & approval_gates(ir)
    reused -= gates | _closure(gates, dependents)
    return [node["id"] for node in ir["nodes"] if node["id"] in reused]


def enclosing_loop(ir: Mapping[str, Any], node_id: str) -> str | None:
    """The loop whose body ``node_id`` can run in, or None outside every loop body.

    Follows the compiler's activation contexts: a route out of a loop node starts
    an iteration, so its targets, and everything they lead to, run inside it.
    """
    nodes = {node["id"]: node for node in ir["nodes"]}
    contexts: dict[str, set[tuple[str, ...]]] = {
        item: (
            {()}
            if node["execution"]["initial_activation"] or not node["readiness"]["needs"]
            else set()
        )
        for item, node in nodes.items()
    }
    changed = True
    while changed:
        changed = False
        for source_id, node in nodes.items():
            routed: set[tuple[str, ...]] = set()
            for context in contexts[source_id]:
                if node["type"] == "loop" and source_id not in context:
                    routed.add((*context, source_id))
                else:
                    routed.add(context)
            for route in node["routes"]:
                target = route.get("target")
                if target not in contexts:
                    continue
                before = len(contexts[target])
                contexts[target].update(routed)
                changed = changed or len(contexts[target]) != before
    for context in sorted(contexts.get(node_id, ()), key=len, reverse=True):
        if context:
            return context[-1]
    return None


def node_fingerprints(ir: Mapping[str, Any], node_ids: Iterable[str]) -> dict[str, str]:
    """A fingerprint per node covering its definition and everything it depends on."""
    nodes = {node["id"]: node for node in ir["nodes"]}
    dependencies = node_dependencies(ir)
    inputs = {item["name"]: item for item in ir["workflow"].get("inputs") or []}
    definitions = {
        item: {key: value for key, value in node.items() if key != "source_span"}
        for item, node in nodes.items()
    }
    fingerprints: dict[str, str] = {}
    for node_id in node_ids:
        if node_id not in nodes:
            continue
        covered = sorted(_closure([node_id], dependencies) | {node_id})
        read: set[str] = set()
        for item in covered:
            _input_references(definitions[item], read)
        fingerprints[node_id] = json_fingerprint(
            {
                "nodes": {item: definitions[item] for item in covered},
                "inputs": {name: inputs[name] for name in sorted(read) if name in inputs},
            }
        )
    return fingerprints


def run_from_refusal(ir: Mapping[str, Any], node_id: str) -> str | None:
    """Why no run can start from ``node_id`` in this compiled source, or None."""
    nodes = {node["id"]: node for node in ir["nodes"]}
    if node_id not in nodes:
        return f"The workflow has no node {node_id!r}."
    loop = enclosing_loop(ir, node_id)
    if loop is not None:
        return (
            f"Node {node_id!r} runs inside loop {loop!r}, and Run from here can't rerun "
            f"one loop iteration yet. Run from {loop!r} instead, or start a new run."
        )
    for reused in reused_nodes(ir, node_id):
        if (nodes[reused].get("configuration") or {}).get("memory") == "run":
            return (
                f"Agent {reused!r} remembers earlier turns only while its run is going, "
                "and those turns aren't saved, so its result can't be reused. "
                "Start a new run instead."
            )
    return None


def _reached(document: Mapping[str, Any], node_id: str) -> bool:
    if any(record.get("node_id") == node_id for record in document.get("runs") or []):
        return True
    return any(
        event.get("node_id") == node_id and event.get("type") == "node_started"
        for event in document.get("events") or []
    )


def _changed_node(
    snapshot: Mapping[str, Any], reused: list[str], current: Mapping[str, str]
) -> str | None:
    """The first reused node whose fingerprint differs in ``snapshot``."""
    try:
        earlier = node_fingerprints(snapshot, reused)
    except (KeyError, TypeError, AttributeError):
        return reused[0] if reused else None
    for node_id in reused:
        if earlier.get(node_id) != current.get(node_id):
            return node_id
    return None


def plan_run_from(
    ir: Mapping[str, Any],
    node_id: str,
    documents: Iterable[Mapping[str, Any]],
    *,
    reuse_run_id: str | None = None,
) -> RunFromPlan:
    """Choose the earlier run a run from ``node_id`` reuses, or refuse.

    ``documents`` are the workflow's run records, newest first, each with
    ``effective_run_status`` applied. With ``reuse_run_id``, only that run is
    considered and every reason it can't be used is reported.
    """
    # Node names are case-insensitive in the source; compiled IDs are lowercase.
    if all(node["id"] != node_id for node in ir["nodes"]):
        node_id = node_id.lower()
    refusal = run_from_refusal(ir, node_id)
    if refusal is not None:
        raise RunFromRefused(refusal)
    reused = reused_nodes(ir, node_id)
    gates = approval_gates(ir)
    gates_in_order = [node["id"] for node in ir["nodes"] if node["id"] in gates]
    current = node_fingerprints(ir, reused)
    first_change: tuple[str, str] | None = None
    first_skip: str | None = None
    named_found = False
    for document in documents:
        run_id = str(document.get("run_id") or "")
        if reuse_run_id is not None and run_id != reuse_run_id:
            continue
        named_found = True
        reason: str | None = None
        if document.get("status") == "running":
            reason = f"Run {run_id} is still going. Wait for it to finish or stop it first."
        elif not _reached(document, node_id):
            reason = f"Run {run_id} never reached node {node_id!r}."
        elif not isinstance(document.get("compiled_snapshot"), Mapping):
            reason = (
                f"Run {run_id} was saved by an older version of Raticode, so its results "
                "can't be reused."
            )
        else:
            values = saved_values_refusal(document)
            if values is not None:
                reason = values.replace("This run", f"Run {run_id}", 1)
        if reason is None:
            changed = _changed_node(document["compiled_snapshot"], reused, current)
            if changed is not None:
                if first_change is None:
                    first_change = (changed, run_id)
                if reuse_run_id is not None:
                    raise RunFromRefused(_changed_message(changed, run_id, node_id))
                continue
            replay = tuple(
                record for record in document.get("runs") or [] if record.get("node_id") in reused
            )
            ask_again = tuple(gate for gate in gates_in_order if _reached(document, gate))
            return RunFromPlan(node_id, document, tuple(reused), replay, ask_again)
        if reuse_run_id is not None:
            raise RunFromRefused(reason)
        if (
            first_skip is None
            and document.get("status") != "running"
            and _reached(document, node_id)
        ):
            first_skip = reason
    if reuse_run_id is not None and not named_found:
        raise RunFromRefused(f"Run {reuse_run_id!r} was not found.")
    if first_change is not None:
        raise RunFromRefused(_changed_message(*first_change, node_id))
    if first_skip is not None:
        raise RunFromRefused(first_skip)
    raise RunFromRefused(
        f"No earlier run reached node {node_id!r}, so there are no results to reuse. "
        "Run the whole workflow first."
    )


def _changed_message(changed: str, run_id: str, node_id: str) -> str:
    return (
        f"Node {changed!r}, upstream of {node_id!r}, changed since run {run_id}, so its "
        "saved result may not fit. Run the whole workflow instead."
    )


def reusable_count(plan: RunFromPlan) -> int:
    """How many of the plan's records a replay can reuse."""
    return sum(1 for record in plan.replay if record.get("outcome") in _REUSABLE)
