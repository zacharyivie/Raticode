from __future__ import annotations

import json
import sys

import typer


def schema_command(
    output_format: str = typer.Option("json", "--format", help="Output format: json."),
) -> None:
    """Print where the machine-readable Rattish language contracts are installed.

    Workflows are written in Rattish.
    """
    if output_format != "json":
        typer.echo(f"Unsupported schema format: {output_format}", err=True)
        raise typer.Exit(2)
    _emit(_rattish_pointer())


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
    }


def _emit(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str))
    sys.stdout.write("\n")
