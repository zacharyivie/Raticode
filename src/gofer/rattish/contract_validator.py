"""Validation for machine-readable Rattish node contracts."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError, ValidationError  # type: ignore[import-untyped]

JsonObject = dict[str, Any]

_PROFILE_ANNOTATIONS = {"$schema", "description", "title"}
_PROFILE_ASSERTIONS = {
    "additionalProperties",
    "const",
    "enum",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "format",
    "maxItems",
    "maxLength",
    "maximum",
    "minItems",
    "minLength",
    "minimum",
    "pattern",
    "required",
    "type",
}
_PROFILE_APPLICATORS = {"anyOf", "items", "properties"}
_PROFILE_KEYWORDS = _PROFILE_ANNOTATIONS | _PROFILE_ASSERTIONS | _PROFILE_APPLICATORS


@dataclass(frozen=True, slots=True)
class ContractValidationIssue:
    """One stable contract-validation failure."""

    code: str
    file: Path
    path: tuple[str | int, ...]
    message: str

    def render(self) -> str:
        """Render a compact CLI diagnostic."""
        location = "$"
        for part in self.path:
            location += f"[{part}]" if isinstance(part, int) else f".{part}"
        return f"{self.file}:{location}: {self.code}: {self.message}"


class ContractValidationError(ValueError):
    """Raised when one or more node contracts are invalid."""

    def __init__(self, issues: Iterable[ContractValidationIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__(f"{len(self.issues)} Rattish contract validation issue(s)")


def _load_json_object(path: Path) -> tuple[JsonObject | None, list[ContractValidationIssue]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, [
            ContractValidationIssue(
                code="RATTISH_CONTRACT_JSON_INVALID",
                file=path,
                path=(),
                message=str(exc),
            )
        ]

    if not isinstance(value, dict):
        return None, [
            ContractValidationIssue(
                code="RATTISH_CONTRACT_INVALID",
                file=path,
                path=(),
                message="Contract document must be a JSON object.",
            )
        ]
    return value, []


def _validation_issue(path: Path, exc: ValidationError) -> ContractValidationIssue:
    return ContractValidationIssue(
        code="RATTISH_CONTRACT_INVALID",
        file=path,
        path=tuple(exc.absolute_path),
        message=exc.message,
    )


def _schema_issue(
    path: Path,
    field_path: tuple[str | int, ...],
    exc: SchemaError,
) -> ContractValidationIssue:
    return ContractValidationIssue(
        code="RATTISH_CONTRACT_SCHEMA_INVALID",
        file=path,
        path=field_path + tuple(exc.absolute_path),
        message=exc.message,
    )


def _embedded_schemas(contract: Mapping[str, Any]) -> Iterable[tuple[tuple[str | int, ...], Any]]:
    yield ("configuration_schema",), contract.get("configuration_schema")

    success_output = contract.get("success_output")
    if isinstance(success_output, Mapping):
        if success_output.get("kind") == "fixed":
            yield ("success_output", "schema"), success_output.get("schema")
        elif success_output.get("kind") == "configuration_selected":
            yield ("success_output", "default_schema"), success_output.get("default_schema")

    input_ports = contract.get("input_ports")
    if not isinstance(input_ports, Mapping):
        return
    ports = input_ports.get("ports")
    if isinstance(ports, Mapping):
        for name, port in ports.items():
            if isinstance(port, Mapping):
                yield ("input_ports", "ports", str(name), "schema"), port.get("schema")
    if "additional_port_schema" in input_ports:
        yield ("input_ports", "additional_port_schema"), input_ports.get("additional_port_schema")


def _data_schemas(contract: Mapping[str, Any]) -> Iterable[tuple[tuple[str | int, ...], Any]]:
    success_output = contract.get("success_output")
    if isinstance(success_output, Mapping):
        if success_output.get("kind") == "fixed":
            yield ("success_output", "schema"), success_output.get("schema")
        elif success_output.get("kind") == "configuration_selected":
            yield ("success_output", "default_schema"), success_output.get("default_schema")

    input_ports = contract.get("input_ports")
    if not isinstance(input_ports, Mapping):
        return
    ports = input_ports.get("ports")
    if isinstance(ports, Mapping):
        for name, port in ports.items():
            if isinstance(port, Mapping):
                yield ("input_ports", "ports", str(name), "schema"), port.get("schema")
    if "additional_port_schema" in input_ports:
        yield ("input_ports", "additional_port_schema"), input_ports.get("additional_port_schema")


def _profile_issues(
    file: Path,
    schema: Mapping[str, Any],
    path: tuple[str | int, ...],
) -> list[ContractValidationIssue]:
    issues: list[ContractValidationIssue] = []
    for keyword in schema:
        if keyword not in _PROFILE_KEYWORDS:
            issues.append(
                ContractValidationIssue(
                    code="RATTISH_CONTRACT_SCHEMA_PROFILE_UNSUPPORTED",
                    file=file,
                    path=path + (str(keyword),),
                    message=f"Keyword {keyword!r} is outside Rattish Schema Profile 1.",
                )
            )

    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for name, child in properties.items():
            if isinstance(child, Mapping):
                issues.extend(_profile_issues(file, child, path + ("properties", str(name))))

    items = schema.get("items")
    if isinstance(items, Mapping):
        issues.extend(_profile_issues(file, items, path + ("items",)))

    additional_properties = schema.get("additionalProperties")
    if isinstance(additional_properties, Mapping):
        issues.extend(
            _profile_issues(file, additional_properties, path + ("additionalProperties",))
        )

    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        nullable = len(any_of) == 2 and any(
            isinstance(branch, Mapping) and branch.get("type") == "null" for branch in any_of
        )
        if not nullable:
            issues.append(
                ContractValidationIssue(
                    code="RATTISH_CONTRACT_SCHEMA_PROFILE_UNSUPPORTED",
                    file=file,
                    path=path + ("anyOf",),
                    message="Profile 1 permits anyOf only for one value schema plus null.",
                )
            )
        for index, branch in enumerate(any_of):
            if isinstance(branch, Mapping):
                issues.extend(_profile_issues(file, branch, path + ("anyOf", index)))
    return issues


def _validate_embedded_schemas(
    path: Path, contract: Mapping[str, Any]
) -> list[ContractValidationIssue]:
    issues: list[ContractValidationIssue] = []
    for field_path, schema in _embedded_schemas(contract):
        if not isinstance(schema, dict):
            continue
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            issues.append(_schema_issue(path, field_path, exc))
    return issues


def _validate_data_schema_profiles(
    path: Path, contract: Mapping[str, Any]
) -> list[ContractValidationIssue]:
    issues: list[ContractValidationIssue] = []
    for field_path, schema in _data_schemas(contract):
        if isinstance(schema, Mapping):
            issues.extend(_profile_issues(path, schema, field_path))
    return issues


def _validate_default_fields(
    path: Path, contract: Mapping[str, Any]
) -> list[ContractValidationIssue]:
    configuration_schema = contract.get("configuration_schema")
    if not isinstance(configuration_schema, Mapping):
        return []
    properties = configuration_schema.get("properties")
    if not isinstance(properties, Mapping):
        properties = {}

    issues: list[ContractValidationIssue] = []
    for collection_name in ("defaults", "computed_defaults"):
        fields = contract.get(collection_name)
        if not isinstance(fields, Mapping):
            continue
        for field_name in fields:
            if field_name not in properties:
                issues.append(
                    ContractValidationIssue(
                        code="RATTISH_CONTRACT_UNKNOWN_DEFAULT_FIELD",
                        file=path,
                        path=(collection_name, str(field_name)),
                        message=(
                            f"Field {field_name!r} is absent from configuration_schema.properties."
                        ),
                    )
                )

    defaults = contract.get("defaults")
    if not isinstance(defaults, Mapping):
        return issues
    for field_name, value in defaults.items():
        field_schema = properties.get(field_name)
        if not isinstance(field_schema, dict):
            continue
        for exc in Draft202012Validator(field_schema).iter_errors(value):
            issues.append(
                ContractValidationIssue(
                    code="RATTISH_CONTRACT_DEFAULT_INVALID",
                    file=path,
                    path=("defaults", str(field_name)) + tuple(exc.absolute_path),
                    message=exc.message,
                )
            )
    return issues


def _validate_configuration_completeness(
    path: Path, contract: Mapping[str, Any]
) -> list[ContractValidationIssue]:
    configuration_schema = contract.get("configuration_schema")
    if not isinstance(configuration_schema, Mapping):
        return []
    defaults = contract.get("defaults", {})
    computed_defaults = contract.get("computed_defaults", {})
    top_level_defaults: set[str] = set()
    if isinstance(defaults, Mapping):
        top_level_defaults.update(str(name) for name in defaults)
    if isinstance(computed_defaults, Mapping):
        top_level_defaults.update(str(name) for name in computed_defaults)
    issues: list[ContractValidationIssue] = []

    def visit(
        schema: Mapping[str, Any],
        schema_path: tuple[str | int, ...],
        *,
        require_complete: bool,
    ) -> None:
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            return
        required = set(schema.get("required", []))
        for name, child in properties.items():
            if not isinstance(child, Mapping):
                continue
            declared_elsewhere = (
                schema_path == ("configuration_schema",) and name in top_level_defaults
            )
            inline_default = (
                schema.get("x-rattish-apply-property-defaults") is True and "default" in child
            )
            if (
                require_complete
                and name not in required
                and not inline_default
                and not declared_elsewhere
                and child.get("x-rattish-allow-absence") is not True
            ):
                issues.append(
                    ContractValidationIssue(
                        code="RATTISH_CONTRACT_OPTIONAL_FIELD_UNSPECIFIED",
                        file=path,
                        path=schema_path + ("properties", str(name)),
                        message=(
                            f"Optional runtime field {name!r} needs a default or "
                            "x-rattish-allow-absence: true."
                        ),
                    )
                )
            visit(
                child,
                schema_path + ("properties", str(name)),
                require_complete=child.get("x-rattish-apply-property-defaults") is True,
            )

    visit(configuration_schema, ("configuration_schema",), require_complete=True)
    return issues


def _validate_unique_named_entries(
    path: Path,
    contract: Mapping[str, Any],
    collection_name: str,
    key_name: str,
) -> list[ContractValidationIssue]:
    entries = contract.get(collection_name)
    if not isinstance(entries, list):
        return []

    issues: list[ContractValidationIssue] = []
    seen: dict[str, int] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            continue
        value = entry.get(key_name)
        if not isinstance(value, str):
            continue
        if value in seen:
            issues.append(
                ContractValidationIssue(
                    code="RATTISH_CONTRACT_DUPLICATE_ENTRY",
                    file=path,
                    path=(collection_name, index, key_name),
                    message=(
                        f"Duplicate {key_name} {value!r}; first declared at index {seen[value]}."
                    ),
                )
            )
        else:
            seen[value] = index
    return issues


def _validate_contract_invariants(
    path: Path, contract: Mapping[str, Any]
) -> list[ContractValidationIssue]:
    issues = _validate_embedded_schemas(path, contract)
    issues.extend(_validate_data_schema_profiles(path, contract))
    issues.extend(_validate_default_fields(path, contract))
    issues.extend(_validate_configuration_completeness(path, contract))
    issues.extend(_validate_unique_named_entries(path, contract, "preflight_checks", "id"))
    issues.extend(_validate_unique_named_entries(path, contract, "static_diagnostics", "code"))
    return issues


def validate_contract_files(
    meta_schema_path: Path,
    contract_paths: Sequence[Path],
) -> tuple[JsonObject, ...]:
    """Validate contracts and return their decoded documents.

    All files are checked before the function raises, so one invocation reports the full
    set of independent contract failures.
    """
    meta_schema, issues = _load_json_object(meta_schema_path)
    if meta_schema is None:
        raise ContractValidationError(issues)

    try:
        Draft202012Validator.check_schema(meta_schema)
    except SchemaError as exc:
        raise ContractValidationError(
            [
                ContractValidationIssue(
                    code="RATTISH_CONTRACT_META_SCHEMA_INVALID",
                    file=meta_schema_path,
                    path=tuple(exc.absolute_path),
                    message=exc.message,
                )
            ]
        ) from exc

    validator = Draft202012Validator(meta_schema)
    documents: list[JsonObject] = []
    identities: dict[tuple[str, int], Path] = {}

    for contract_path in contract_paths:
        contract, load_issues = _load_json_object(contract_path)
        issues.extend(load_issues)
        if contract is None:
            continue
        documents.append(contract)

        structural_errors = sorted(
            validator.iter_errors(contract),
            key=lambda exc: tuple(str(part) for part in exc.absolute_path),
        )
        issues.extend(_validation_issue(contract_path, exc) for exc in structural_errors)
        if structural_errors:
            continue

        issues.extend(_validate_contract_invariants(contract_path, contract))

        node_type = contract["node_type"]
        contract_version = contract["contract_version"]
        identity = (node_type, contract_version)
        previous_path = identities.get(identity)
        if previous_path is not None:
            issues.append(
                ContractValidationIssue(
                    code="RATTISH_CONTRACT_DUPLICATE_IDENTITY",
                    file=contract_path,
                    path=("node_type",),
                    message=(
                        f"Contract {node_type!r} version {contract_version} is already declared "
                        f"by {previous_path}."
                    ),
                )
            )
        else:
            identities[identity] = contract_path

    if issues:
        raise ContractValidationError(issues)
    return tuple(documents)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Rattish node contract JSON files.")
    parser.add_argument("--schema", required=True, type=Path, help="Node-contract meta-schema.")
    parser.add_argument("contracts", nargs="+", type=Path, help="Contract JSON files.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the contract validator CLI."""
    args = _parser().parse_args(argv)
    try:
        contracts = validate_contract_files(args.schema, args.contracts)
    except ContractValidationError as exc:
        for issue in exc.issues:
            print(issue.render())
        return 1
    print(f"Validated {len(contracts)} Rattish node contract(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
