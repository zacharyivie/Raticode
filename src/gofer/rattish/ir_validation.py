"""Independent semantic validation for executable Rattish IR."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped]

from gofer.rattish.schema_compat import instance_matches_schema, schema_accepts_schema

SUPPORTED_IR_VERSION = 1
SUPPORTED_RATTISH_VERSION = 1


class InvalidRattishIrError(ValueError):
    """Raised before execution when a Rattish IR document is invalid."""


def _immutable(*args: Any, **kwargs: Any) -> None:
    _ = args, kwargs
    raise TypeError("Validated Rattish IR is immutable.")


class _FrozenDict(dict[str, Any]):
    def __deepcopy__(self, memo: dict[int, Any]) -> _FrozenDict:
        _ = memo
        return self

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable  # type: ignore[assignment]
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable  # type: ignore[assignment]


class _FrozenList(list[Any]):
    def __deepcopy__(self, memo: dict[int, Any]) -> _FrozenList:
        _ = memo
        return self

    __setitem__ = _immutable
    __delitem__ = _immutable
    append = _immutable
    clear = _immutable
    extend = _immutable
    insert = _immutable
    pop = _immutable
    remove = _immutable
    reverse = _immutable
    sort = _immutable
    __iadd__ = _immutable  # type: ignore[assignment]
    __imul__ = _immutable  # type: ignore[assignment]


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        frozen = _FrozenDict()
        dict.__init__(frozen, {str(key): _freeze_json(item) for key, item in value.items()})
        return frozen
    if isinstance(value, list):
        frozen_list = _FrozenList()
        list.__init__(frozen_list, (_freeze_json(item) for item in value))
        return frozen_list
    return value


class ValidatedRattishIR(_FrozenDict):
    """Immutable IR created only after schema and semantic validation."""

    _VALIDATION_TOKEN = object()

    def __init__(
        self, document: Mapping[str, Any], *, _validation_token: object | None = None
    ) -> None:
        if _validation_token is not self._VALIDATION_TOKEN:
            raise InvalidRattishIrError(
                "Use the Rattish compiler or load_ir() to create validated IR."
            )
        dict.__init__(
            self,
            {str(key): _freeze_json(value) for key, value in document.items()},
        )

    @classmethod
    def _from_validated(cls, document: Mapping[str, Any]) -> ValidatedRattishIR:
        return cls(document, _validation_token=cls._VALIDATION_TOKEN)


def validate_ir_versions(document: Mapping[str, Any]) -> None:
    """Reject documents whose execution contract is not supported by this runtime."""
    ir_version = document.get("ir_version")
    if ir_version is not None and ir_version != SUPPORTED_IR_VERSION:
        raise InvalidRattishIrError(
            f"Unsupported Rattish IR version {ir_version!r}; "
            f"this runtime supports version {SUPPORTED_IR_VERSION}."
        )
    rattish_version = document.get("rattish_version")
    if rattish_version is not None and rattish_version != SUPPORTED_RATTISH_VERSION:
        raise InvalidRattishIrError(
            f"Unsupported Rattish language version {rattish_version!r}; "
            f"this runtime supports version {SUPPORTED_RATTISH_VERSION}."
        )


def validate_ir_invariants(document: Mapping[str, Any]) -> None:
    """Validate graph and reference invariants that JSON Schema cannot express."""
    validate_ir_versions(document)
    nodes = document["nodes"]
    node_ids = [node["id"] for node in nodes]
    duplicates = sorted(name for name, count in Counter(node_ids).items() if count > 1)
    if duplicates:
        _invalid(f"duplicate node IDs: {', '.join(duplicates)}")
    known_nodes = set(node_ids)

    workflow_id = document["workflow"]["id"]
    if document["source"]["workflow_id"] != workflow_id:
        _invalid("source.workflow_id must equal workflow.id")
    dependencies = {
        (item["kind"], item["id"], item["version"], item["fingerprint"])
        for item in document["source"]["dependencies"]
    }

    actual_incoming: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for node in nodes:
        node_id = node["id"]
        if node["contract"]["node_type"] != node["type"]:
            _invalid(f"node {node_id!r} has a contract for a different node type")
        contract_identity = (
            "node_contract",
            node["contract"]["node_type"],
            node["contract"]["version"],
            node["contract"]["fingerprint"],
        )
        if contract_identity not in dependencies:
            _invalid(f"node {node_id!r} has no matching node-contract dependency")
        _validate_schema(node["output"]["schema"], f"node {node_id!r} output")

        needs = node["readiness"]["needs"]
        _require_known_nodes(needs, known_nodes, f"node {node_id!r} needs")
        initial = node["execution"]["initial_activation"]
        if initial != (not needs):
            _invalid(
                f"node {node_id!r} initial_activation must be true exactly when needs is empty"
            )
        if node["execution"]["start_declared"] and needs:
            _invalid(f"node {node_id!r} declares start and needs")
        if node["execution"]["finish"] is not None and node["routes"]:
            _invalid(f"terminal node {node_id!r} cannot have routes")
        if node["execution"]["finish"] == "fail" and node["execution"]["allow_fail"]:
            _invalid(f"finish-fail node {node_id!r} cannot allow failure")

        route_keys: set[tuple[str, str]] = set()
        otherwise_targets: set[str] = set()
        unconditional_targets: set[str] = set()
        for route in node["routes"]:
            target = route["target"]
            _require_known_nodes([target], known_nodes, f"node {node_id!r} route")
            actual_incoming[target].add(node_id)
            key = (target, _predicate_key(route))
            if key in route_keys:
                _invalid(f"node {node_id!r} contains a duplicate route to {target!r}")
            route_keys.add(key)
            if route["mode"] == "otherwise":
                otherwise_targets.add(target)
            elif route["mode"] == "unconditional":
                unconditional_targets.add(target)
            if route["predicate"] is not None:
                _validate_predicate_references(route["predicate"], known_nodes, node_id)
                if not node["execution"]["allow_fail"] and _predicate_matches_failure(
                    route["predicate"]
                ):
                    _invalid(f"node {node_id!r} contains an unreachable failure route")
        if len(otherwise_targets) > 1:
            _invalid(f"node {node_id!r} contains more than one otherwise route")
        contradictory = otherwise_targets & unconditional_targets
        if contradictory:
            _invalid(
                f"node {node_id!r} routes unconditionally and otherwise to "
                f"{', '.join(sorted(contradictory))}"
            )

        binding_names = [binding["name"] for binding in node["bindings"]]
        if len(binding_names) != len(set(binding_names)):
            _invalid(f"node {node_id!r} contains duplicate bindings")
        for binding in node["bindings"]:
            source = binding["source"]
            source_schema = binding["source_schema"]
            destination_schema = binding["destination_schema"]
            _validate_schema(source_schema, f"node {node_id!r} binding source")
            _validate_schema(destination_schema, f"node {node_id!r} binding destination")
            if not schema_accepts_schema(destination_schema, source_schema):
                _invalid(f"node {node_id!r} has an incompatible binding schema")
            if source["kind"] == "reference":
                _validate_reference(source["reference"], known_nodes, node_id)
                if source["reference"]["schema"] != source_schema:
                    _invalid(f"node {node_id!r} binding reference schema is inconsistent")
                if source["reference"]["optional"] and not binding["default"]["present"]:
                    _invalid(f"node {node_id!r} has an optional binding without a default")
            elif source["kind"] == "expression":
                if source_schema.get("type") != "boolean":
                    _invalid(f"node {node_id!r} binding expression is not Boolean")
                if _predicate_contains_status(source["expression"]):
                    _invalid(f"node {node_id!r} binding expression uses route-only status")
                _validate_predicate_references(
                    source["expression"], known_nodes, f"node {node_id!r} binding expression"
                )
            elif not instance_matches_schema(source_schema, source["value"]):
                _invalid(f"node {node_id!r} binding literal violates its source schema")
            default = binding["default"]
            if default["present"] and not instance_matches_schema(
                destination_schema, default["value"]
            ):
                _invalid(f"node {node_id!r} binding default violates its destination schema")

        provider = node["resolutions"]["provider"]
        if provider is not None:
            provider_identity = (
                "provider_contract",
                provider["provider_id"],
                provider["contract_version"],
                provider["contract_fingerprint"],
            )
            if provider_identity not in dependencies:
                _invalid(f"node {node_id!r} has no matching provider-contract dependency")

        workflow = node["resolutions"]["workflow"]
        if workflow is not None:
            workflow_identity = (
                "workflow_interface",
                workflow["workflow_id"],
                workflow["interface_version"],
                workflow["interface_fingerprint"],
            )
            if workflow_identity not in dependencies:
                _invalid(f"node {node_id!r} has no matching workflow-interface dependency")
            if not any(
                item["kind"] == "workflow"
                and item["id"] == workflow["workflow_id"]
                and item["fingerprint"] == workflow["compilation_fingerprint"]
                for item in document["source"]["dependencies"]
            ):
                _invalid(f"node {node_id!r} has no matching compiled-workflow dependency")
            if node["output"]["schema"] != workflow["output_schema"]:
                _invalid(f"node {node_id!r} output does not match its workflow resolution")
            expected_inputs = workflow["input_schema"].get("properties", {})
            for binding in node["bindings"]:
                if binding["delivery"]["kind"] != "workflow_input":
                    continue
                if binding["name"] not in expected_inputs:
                    _invalid(f"node {node_id!r} binds an undeclared child workflow input")

        control = node["control"]
        if control is not None:
            _require_known_nodes(
                [control["loop_node_id"]], known_nodes, f"node {node_id!r} break control"
            )
            target = next(item for item in nodes if item["id"] == control["loop_node_id"])
            if node["type"] != "break" or target["type"] != "loop":
                _invalid(f"node {node_id!r} has invalid break control")
        elif node["type"] == "break":
            _invalid(f"break node {node_id!r} has no break control")

    for node in nodes:
        node_id = node["id"]
        declared = set(node["readiness"]["incoming_route_sources"])
        if declared != actual_incoming[node_id]:
            _invalid(f"node {node_id!r} incoming_route_sources do not match the route graph")

    source_map_nodes = set(document["source_map"]["nodes"])
    if source_map_nodes != known_nodes:
        _invalid("source_map.nodes must contain exactly the compiled node IDs")

    input_names = [item["name"] for item in document["workflow"]["inputs"]]
    if len(input_names) != len(set(input_names)):
        _invalid("workflow contains duplicate input names")
    for input_declaration in document["workflow"]["inputs"]:
        _validate_schema(input_declaration["schema"], "workflow input")
        default = input_declaration["default"]
        if default["present"] and not instance_matches_schema(
            input_declaration["schema"], default["value"]
        ):
            _invalid(f"workflow input {input_declaration['name']!r} default violates its schema")
    output_names = [item["name"] for item in document["workflow"]["outputs"]]
    if len(output_names) != len(set(output_names)):
        _invalid("workflow contains duplicate output names")
    for output in document["workflow"]["outputs"]:
        _validate_schema(output["schema"], "workflow output")
        _validate_reference(output["source"], known_nodes, "workflow output")
        if output["source"]["root"] in {"secret", "trigger"}:
            _invalid(f"workflow output {output['name']!r} uses execution-local or secret reference")
        if not schema_accepts_schema(output["schema"], output["source"]["schema"]):
            _invalid(f"workflow output {output['name']!r} source violates its public schema")


def _validate_predicate_references(
    predicate: Mapping[str, Any], known_nodes: set[str], owner: str
) -> None:
    kind = predicate["kind"]
    if kind == "logical":
        _validate_predicate_references(predicate["left"], known_nodes, owner)
        _validate_predicate_references(predicate["right"], known_nodes, owner)
    elif kind == "not":
        _validate_predicate_references(predicate["operand"], known_nodes, owner)
    elif kind in {"exists", "null_test", "reference"}:
        _validate_reference(predicate["reference"], known_nodes, owner)
    elif kind == "comparison":
        for operand in (predicate["left"], predicate["right"]):
            if operand["kind"] == "reference":
                _validate_reference(operand["reference"], known_nodes, owner)


def _predicate_matches_failure(predicate: Mapping[str, Any]) -> bool:
    kind = predicate["kind"]
    if kind == "status":
        return bool(predicate["value"] == "failed")
    if kind == "logical":
        return _predicate_matches_failure(predicate["left"]) or _predicate_matches_failure(
            predicate["right"]
        )
    if kind == "not":
        return _predicate_matches_success(predicate["operand"])
    return False


def _predicate_contains_status(predicate: Mapping[str, Any]) -> bool:
    kind = predicate["kind"]
    if kind == "status":
        return True
    if kind == "logical":
        return _predicate_contains_status(predicate["left"]) or _predicate_contains_status(
            predicate["right"]
        )
    if kind == "not":
        return _predicate_contains_status(predicate["operand"])
    return False


def _predicate_matches_success(predicate: Mapping[str, Any]) -> bool:
    kind = predicate["kind"]
    if kind == "status":
        return bool(predicate["value"] == "succeeded")
    if kind == "logical":
        return _predicate_matches_success(predicate["left"]) or _predicate_matches_success(
            predicate["right"]
        )
    if kind == "not":
        return _predicate_matches_failure(predicate["operand"])
    return False


def _validate_schema(schema: Mapping[str, Any], owner: str) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        _invalid(f"{owner} contains an invalid JSON Schema: {exc.message}")


def _validate_reference(reference: Mapping[str, Any], known_nodes: set[str], owner: str) -> None:
    root = reference["root"]
    if root == "secret":
        if not reference["symbol"] or reference["channel"] is not None or reference["path"]:
            _invalid(f"{owner!r} contains an invalid secret reference")
        return
    if root == "trigger":
        if reference["symbol"] != "events" or reference["channel"] is not None:
            _invalid(f"{owner!r} contains an invalid trigger reference")
        return
    if root == "input":
        if not reference["symbol"] or reference["channel"] is not None:
            _invalid(f"{owner!r} contains an invalid input reference")
        return
    if root != "node":
        _invalid(f"{owner!r} contains unsupported reference root {root!r}")
        return
    symbol = reference["symbol"]
    if symbol not in known_nodes:
        _invalid(f"{owner!r} references unknown node {symbol!r}")


def _require_known_nodes(values: Iterable[str], known: set[str], owner: str) -> None:
    unknown = sorted(set(values) - known)
    if unknown:
        _invalid(f"{owner} references unknown node(s): {', '.join(unknown)}")


def _predicate_key(route: Mapping[str, Any]) -> str:
    predicate = json.dumps(route["predicate"], sort_keys=True, separators=(",", ":"))
    return f"{route['mode']}:{predicate}"


def _invalid(message: str) -> None:
    raise InvalidRattishIrError(f"Invalid Rattish IR invariant: {message}.")
