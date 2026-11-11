from __future__ import annotations

import json
import sys
from typing import Any

import typer

from gofer.core.authoring import OPERATION_MODELS, authoring_contract, operation_field_enum_values
from gofer.core.graph import EdgeConditionType

_COMMAND_ENUM_FIELDS = {
    "resource_type": "resource_type",
    "task": "task",
    "vector_mode": "mode",
    "memory": "memory",
    "http_response_mode": "response_mode",
    "approval_timeout_decision": "timeout_decision",
    "notification_channel": "channel",
    "notification_urgency": "urgency",
}


def _json_default(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return list(value)
    return str(value)


def _parameter_contract(parameter: Any, command_id: str) -> dict[str, object]:
    option_names = list(getattr(parameter, "opts", ())) + list(
        getattr(parameter, "secondary_opts", ())
    )
    contract: dict[str, object] = {
        "id": f"mutation.{command_id}.{parameter.name}",
        "name": parameter.name,
        "options": option_names,
        "required": parameter.required,
        "default": _json_default(parameter.default),
        "type": parameter.type.name,
        "help": getattr(parameter, "help", None),
    }
    if parameter.name == "condition":
        contract["enum"] = [item.value for item in EdgeConditionType]
    elif parameter.name == "node_type":
        contract["enum"] = sorted(OPERATION_MODELS)
    elif parameter.name == "fan_source":
        contract["enum"] = ["count", "tabular", "directory", "trigger-events", "infinite"]
    elif parameter.name in _COMMAND_ENUM_FIELDS:
        contract["enum"] = operation_field_enum_values(_COMMAND_ENUM_FIELDS[parameter.name])
    for name in ("min", "max", "min_open", "max_open"):
        value = getattr(parameter.type, name, None)
        if value is not None:
            contract[name] = value
    return contract


def mutation_command_contract(command_id: str) -> dict[str, object]:
    from gofer.cli.main import app

    command: Any = typer.main.get_command(app)
    parts = command_id.split(".")
    for part in parts:
        commands = getattr(command, "commands", {})
        if part not in commands:
            raise KeyError(f"Unsupported mutation command: {command_id}")
        command = commands[part]
    return {
        "id": f"mutation.{command_id}",
        "command": "Raticode " + " ".join(parts),
        "description": command.help,
        "parameters": [_parameter_contract(param, command_id) for param in command.params],
    }


def schema_command(
    output_format: str = typer.Option("json", "--format", help="Output format: json."),
    legacy_toml: bool = typer.Option(
        False,
        "--legacy-toml",
        help="Print the legacy TOML authoring contract instead of the Rattish pointers.",
    ),
    operation: str | None = typer.Option(
        None, "--operation", help="Legacy TOML: the contract for one operation type."
    ),
    capability: str | None = typer.Option(
        None, "--capability", help="Legacy TOML: one model capability schema."
    ),
    command: str | None = typer.Option(
        None,
        "--command",
        help="Legacy TOML: structured help for a mutation command, e.g. workflow.add-node.",
    ),
) -> None:
    """Print where the machine-readable Rattish language contracts are installed.

    Workflows are written in Rattish. The legacy TOML authoring contract is still
    available with --legacy-toml, --operation, --capability or --command.
    """
    if output_format != "json":
        typer.echo(f"Unsupported schema format: {output_format}", err=True)
        raise typer.Exit(2)
    if sum(item is not None for item in (operation, capability, command)) > 1:
        typer.echo("Use only one of --operation, --capability, or --command", err=True)
        raise typer.Exit(2)
    if not (legacy_toml or operation or capability or command):
        _emit(_rattish_pointer())
        return
    try:
        payload = authoring_contract(operation=operation, capability=capability)
        if command is not None:
            payload = {
                "metadata": payload["metadata"],
                "mutation_command": mutation_command_contract(command),
            }
        elif operation is None and capability is None:
            payload["mutation_commands"] = [
                mutation_command_contract("workflow.add-edge"),
                mutation_command_contract("workflow.add-node"),
            ]
    except KeyError as exc:
        typer.echo(str(exc).strip("'"), err=True)
        raise typer.Exit(2)
    payload["legacy"] = True
    _emit(payload)


def _rattish_pointer() -> dict[str, object]:
    from gofer.rattish.artifacts import (
        RattishArtifactError,
        rattish_asset_root,
        rattish_docs_root,
    )

    try:
        docs_root = rattish_docs_root()
        asset_root = rattish_asset_root()
    except RattishArtifactError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    return {
        "command": "schema",
        "ok": True,
        "language": "rattish",
        "source": "workflow.rattish",
        "docsRoot": str(docs_root),
        "overview": str(docs_root / "README.md"),
        "grammar": str(docs_root / "grammar.ebnf"),
        "staticSemantics": str(docs_root / "static-semantics.md"),
        "nodeContractsGuide": str(docs_root / "node-contracts.md"),
        "contractsRoot": str(asset_root / "contracts"),
        "schemasRoot": str(asset_root / "schemas"),
        "commands": {
            "docs": "Raticode rattish docs --format json",
            "create": "Raticode rattish create NAME --project PATH --format json",
            "check": "Raticode rattish check PATH --format json",
            "preflight": "Raticode rattish preflight PATH --format json",
        },
        "legacyToml": "Raticode schema --legacy-toml prints the old TOML authoring contract.",
    }


def _emit(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str))
    sys.stdout.write("\n")
