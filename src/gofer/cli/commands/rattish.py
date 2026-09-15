from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import typer

from gofer.rattish.artifacts import (
    CompiledArtifact,
    RattishArtifactError,
    compile_rattish_file,
    rattish_asset_root,
    rattish_docs_root,
)
from gofer.rattish.diagnostics import RattishDiagnostic, RattishError, SourcePosition, SourceSpan
from gofer.rattish.formatter import RattishFormatError, format_rattish_file
from gofer.rattish.preflight import run_preflight
from gofer.rattish.run_service import (
    RattishRunArtifactError,
    RattishRunResult,
    run_rattish_file,
)
from gofer.rattish.workspaces import (
    RattishWorkspaceError,
    create_registered_workflow,
    list_registered_workflows,
)

app = typer.Typer(help="Check Rattish workflows and deployment readiness", no_args_is_help=True)


@app.command("docs")
def docs(
    output_format: str = typer.Option("text", "--format", help="Output format: text or json."),
) -> None:
    """Print paths to the installed Rattish documentation and node contracts."""
    _require_format(output_format)
    try:
        docs_root = rattish_docs_root()
        asset_root = rattish_asset_root()
    except RattishArtifactError as exc:
        if output_format == "json":
            _emit_json({"command": "docs", "ok": False, "error": str(exc)})
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    payload = {
        "command": "docs",
        "ok": True,
        "docsRoot": str(docs_root),
        "overview": str(docs_root / "README.md"),
        "grammar": str(docs_root / "grammar.ebnf"),
        "staticSemantics": str(docs_root / "static-semantics.md"),
        "nodeContractsGuide": str(docs_root / "node-contracts.md"),
        "contractsRoot": str(asset_root / "contracts"),
        "schemasRoot": str(asset_root / "schemas"),
    }
    if output_format == "json":
        _emit_json(payload)
    else:
        typer.echo(docs_root)


@app.command("create")
def create(
    name: str = typer.Argument(..., help="Human-readable workflow name."),
    project: Path = typer.Option(
        Path("."), "--project", help="Repository project folder that will own the workflow."
    ),
    output_format: str = typer.Option("text", "--format", help="Output format: text or json."),
    registry_dir: Path | None = typer.Option(
        None, "--registry-dir", help="Override the app directory used for workflow registration."
    ),
) -> None:
    """Create and register a workflow in PROJECT/.raticode/."""
    _require_format(output_format)
    try:
        workflow = create_registered_workflow(project, name, registry_dir=registry_dir)
    except RattishWorkspaceError as exc:
        _emit_workspace_error("create", project, exc, output_format)
        raise typer.Exit(2)
    payload = {"command": "create", "ok": True, "workflow": workflow.to_payload()}
    if output_format == "json":
        _emit_json(payload)
    else:
        typer.echo(f"Created {workflow.workflow_id} in {workflow.workflow_root}")


@app.command("list")
def list_workflows(
    output_format: str = typer.Option("text", "--format", help="Output format: text or json."),
    registry_dir: Path | None = typer.Option(
        None, "--registry-dir", help="Override the app directory used for workflow registration."
    ),
) -> None:
    """List registered Rattish workflows grouped by repository project."""
    _require_format(output_format)
    try:
        workflows = list_registered_workflows(registry_dir=registry_dir)
    except RattishWorkspaceError as exc:
        _emit_workspace_error("list", Path("."), exc, output_format)
        raise typer.Exit(2)
    projects: dict[str, dict[str, Any]] = {}
    for workflow in workflows:
        key = str(workflow.project_root)
        project = projects.setdefault(
            key,
            {
                "root": key,
                "name": workflow.project_root.name or key,
                "workflows": [],
            },
        )
        project["workflows"].append(workflow.to_payload())
    payload = {"command": "list", "ok": True, "projects": list(projects.values())}
    if output_format == "json":
        _emit_json(payload)
        return
    if not projects:
        typer.echo("No registered Rattish workflows.")
        return
    for project in projects.values():
        typer.echo(f"{project['name']}  {project['root']}")
        for workflow in project["workflows"]:
            typer.echo(f"  {workflow['id']}  {workflow['name']}")


@app.command("check")
def check(
    source: Path = typer.Argument(..., help="Path to a .rattish workflow file."),
    output_format: str = typer.Option("text", "--format", help="Output format: text or json."),
    data_dir: Path | None = typer.Option(
        None, "--data-dir", help="Override the app data directory used for internal artifacts."
    ),
) -> None:
    """Compile a Rattish file and report language errors without running it."""
    _require_format(output_format)
    artifact = _compile_or_exit(source, data_dir, output_format, "check")
    payload = _payload("check", source, artifact, list(artifact.diagnostics))
    _emit(payload, output_format)


@app.command("compile")
def compile_source(
    source: Path = typer.Argument(..., help="Path to a .rattish workflow file."),
    output_format: str = typer.Option("text", "--format", help="Output format: text or json."),
    data_dir: Path | None = typer.Option(
        None, "--data-dir", help="Override the app data directory used for internal artifacts."
    ),
) -> None:
    """Compile and publish validated IR to Raticode's internal artifact cache."""
    _require_format(output_format)
    artifact = _compile_or_exit(source, data_dir, output_format, "compile")
    payload = _payload("compile", source, artifact, list(artifact.diagnostics))
    payload["ir"] = {
        "version": artifact.ir["ir_version"],
        "workflow_id": artifact.ir["workflow"]["id"],
        "fingerprint": artifact.ir["source"]["compilation_fingerprint"],
    }
    _emit(payload, output_format)


@app.command("inspect-ir")
def inspect_ir(
    source: Path = typer.Argument(..., help="Path to a .rattish workflow file."),
    pretty: bool = typer.Option(False, "--pretty", help="Indent the emitted JSON."),
    data_dir: Path | None = typer.Option(
        None, "--data-dir", help="Override the app data directory used for internal artifacts."
    ),
) -> None:
    """Print validated internal IR without creating a source-side IR file."""
    artifact = _compile_or_exit(source, data_dir, "text", "inspect-ir")
    if pretty:
        typer.echo(json.dumps(artifact.ir, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        _emit_json(artifact.ir)


@app.command("format")
def format_source(
    source: Path = typer.Argument(..., help="Path to a .rattish workflow file."),
    check_only: bool = typer.Option(
        False, "--check", help="Report whether formatting is needed without writing the file."
    ),
    stdout: bool = typer.Option(False, "--stdout", help="Write formatted source to stdout."),
) -> None:
    """Canonicalize Rattish spelling and layout while preserving comments."""
    if check_only and stdout:
        typer.echo("Use either --check or --stdout, not both.", err=True)
        raise typer.Exit(2)
    try:
        result = format_rattish_file(source, write=not check_only and not stdout)
    except RattishError as exc:
        diagnostics = [item.to_json() for item in exc.diagnostics]
        _emit(_error_payload("format", source, diagnostics), "text")
        raise typer.Exit(1)
    except RattishFormatError as exc:
        diagnostic = _artifact_diagnostic(source, exc).to_json()
        _emit(_error_payload("format", source, [diagnostic]), "text")
        raise typer.Exit(2)

    if stdout:
        sys.stdout.write(result.source)
        return
    resolved = source.expanduser().resolve()
    if check_only and result.changed:
        typer.echo(f"{resolved}: needs formatting", err=True)
        raise typer.Exit(1)
    if check_only:
        typer.echo(f"{resolved}: formatted")
    elif result.changed:
        typer.echo(f"{resolved}: reformatted")
    else:
        typer.echo(f"{resolved}: already formatted")


@app.command("preflight")
def preflight(
    source: Path = typer.Argument(..., help="Path to a .rattish workflow file."),
    output_format: str = typer.Option("text", "--format", help="Output format: text or json."),
    data_dir: Path | None = typer.Option(
        None, "--data-dir", help="Override the app data directory used for internal artifacts."
    ),
) -> None:
    """Compile a Rattish file and check whether this environment can run it."""
    _require_format(output_format)
    artifact = _compile_or_exit(source, data_dir, output_format, "preflight")
    deployment = run_preflight(artifact.ir, data_dir=data_dir)
    diagnostics = [
        *artifact.diagnostics,
        *(item.to_json() for item in deployment.diagnostics),
    ]
    payload = _payload("preflight", source, artifact, diagnostics)
    _emit(payload, output_format)
    if not payload["ok"]:
        raise typer.Exit(1)


@app.command("run")
def run(
    source: Path = typer.Argument(..., help="Path to a .rattish workflow file."),
    input_: list[str] | None = typer.Option(
        None,
        "--input",
        help="Workflow input as NAME=JSON. Repeat for multiple inputs.",
    ),
    output_format: str = typer.Option("text", "--format", help="Output format: text or json."),
    data_dir: Path | None = typer.Option(
        None, "--data-dir", help="Override the app data directory used for runs and artifacts."
    ),
) -> None:
    """Compile, preflight, run, and persist a Rattish workflow."""
    _require_format(output_format)
    try:
        workflow_inputs = _parse_run_inputs(input_ or [])
    except ValueError as exc:
        diagnostic = _run_input_diagnostic(source, exc).to_json()
        _emit(_error_payload("run", source, [diagnostic]), output_format)
        raise typer.Exit(2)
    try:
        result = asyncio.run(
            run_rattish_file(source, workflow_inputs=workflow_inputs, data_dir=data_dir)
        )
    except RattishError as exc:
        diagnostics = [item.to_json() for item in exc.diagnostics]
        _emit(_error_payload("run", source, diagnostics), output_format)
        raise typer.Exit(1)
    except (RattishArtifactError, RattishRunArtifactError) as exc:
        diagnostic = _artifact_diagnostic(source, exc).to_json()
        _emit(_error_payload("run", source, [diagnostic]), output_format)
        raise typer.Exit(2)

    _emit_run(result, output_format)
    if result.status == "invalid_inputs":
        raise typer.Exit(2)
    if not result.ok:
        raise typer.Exit(1)


def _compile_or_exit(
    source: Path,
    data_dir: Path | None,
    output_format: str,
    command: str,
) -> CompiledArtifact:
    try:
        return compile_rattish_file(source, data_dir=data_dir)
    except RattishError as exc:
        diagnostics = [item.to_json() for item in exc.diagnostics]
        _emit(_error_payload(command, source, diagnostics), output_format)
        raise typer.Exit(1)
    except RattishArtifactError as exc:
        diagnostic = _artifact_diagnostic(source, exc).to_json()
        _emit(_error_payload(command, source, [diagnostic]), output_format)
        raise typer.Exit(2)


def _require_format(output_format: str) -> None:
    if output_format not in {"text", "json"}:
        typer.echo("Unsupported format. Use 'text' or 'json'.", err=True)
        raise typer.Exit(2)


def _payload(
    command: str,
    source: Path,
    artifact: CompiledArtifact,
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "command": command,
        "source": str(artifact.source_path),
        "ok": not any(item["severity"] == "error" for item in diagnostics),
        "cache": {
            "hit": artifact.cache_hit,
            "compilation_fingerprint": artifact.ir["source"]["compilation_fingerprint"],
        },
        "diagnostics": diagnostics,
    }


def _error_payload(command: str, source: Path, diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "command": command,
        "source": str(source.expanduser().resolve()),
        "ok": False,
        "cache": None,
        "diagnostics": diagnostics,
    }


def _emit(payload: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        _emit_json(payload)
        return

    source = payload["source"]
    if payload["ok"]:
        cache_label = "cached" if payload["cache"] and payload["cache"]["hit"] else "compiled"
        if payload["command"] == "preflight":
            typer.echo(f"{source}: deployment ready ({cache_label})")
        elif payload["command"] == "compile":
            typer.echo(f"{source}: valid IR ({cache_label})")
        else:
            typer.echo(f"{source}: valid Rattish ({cache_label})")
    else:
        failure = {
            "preflight": "deployment preflight failed",
            "format": "Rattish format failed",
            "compile": "Rattish compilation failed",
            "inspect-ir": "Rattish IR inspection failed",
        }.get(payload["command"], "Rattish check failed")
        typer.echo(f"{source}: {failure}", err=True)
    for diagnostic in payload["diagnostics"]:
        start = diagnostic["span"]["start"]
        file = diagnostic["file"]
        if file == Path(source).name:
            file = source
        line = (
            f"{file}:{start['line']}:{start['column']}: "
            f"{diagnostic['severity']} {diagnostic['code']}: {diagnostic['message']}"
        )
        typer.echo(line, err=diagnostic["severity"] == "error")
        for suggestion in diagnostic["suggestions"]:
            typer.echo(f"  suggestion: {suggestion}", err=diagnostic["severity"] == "error")


def _artifact_diagnostic(
    source: Path, exc: RattishArtifactError | RattishRunArtifactError | RattishFormatError
) -> RattishDiagnostic:
    position = SourcePosition(offset=0, line=1, column=1)
    return RattishDiagnostic(
        code="RATTISH_ARTIFACT_IO_ERROR",
        severity="error",
        phase="lowering",
        message=str(exc),
        file=str(source.expanduser().resolve()),
        span=SourceSpan(position, position),
    )


def _run_input_diagnostic(source: Path, exc: ValueError) -> RattishDiagnostic:
    position = SourcePosition(offset=0, line=1, column=1)
    return RattishDiagnostic(
        code="RATTISH_CLI_INPUT_INVALID",
        severity="error",
        phase="runtime",
        message=str(exc),
        file=str(source.expanduser().resolve()),
        span=SourceSpan(position, position),
        suggestions=("Use --input NAME=JSON, for example --input count=3.",),
    )


def _parse_run_inputs(values: list[str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for value in values:
        name, separator, raw = value.partition("=")
        name = name.strip()
        if not separator or not name:
            raise ValueError(f"Invalid workflow input {value!r}; expected NAME=JSON.")
        if name in parsed:
            raise ValueError(f"Workflow input {name!r} was supplied more than once.")
        try:
            parsed[name] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Workflow input {name!r} is not valid JSON: {exc.msg}.") from exc
    return parsed


def _emit_run(result: RattishRunResult, output_format: str) -> None:
    payload = {
        "command": "run",
        "source": result.document["source"]["path"],
        "ok": result.ok,
        "artifact_path": str(result.path),
        "run": result.document,
    }
    if output_format == "json":
        _emit_json(payload)
        return
    typer.echo(f"{payload['source']}: run {result.document['run_id']} {result.status}")
    typer.echo(f"Run artifact: {result.path}")
    for diagnostic in result.document["diagnostics"]:
        start = diagnostic["span"]["start"]
        typer.echo(
            f"{diagnostic['file']}:{start['line']}:{start['column']}: "
            f"{diagnostic['severity']} {diagnostic['code']}: {diagnostic['message']}",
            err=diagnostic["severity"] == "error",
        )
    error = result.document["error"]
    if error is not None:
        typer.echo(f"{error['code']}: {error['message']}", err=True)
    if result.document["outputs"]:
        typer.echo(json.dumps(result.document["outputs"], sort_keys=True))


def _emit_json(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    sys.stdout.write("\n")


def _emit_workspace_error(
    command: str,
    project: Path,
    exc: RattishWorkspaceError,
    output_format: str,
) -> None:
    payload = {
        "command": command,
        "ok": False,
        "project": str(project.expanduser().resolve()),
        "error": {"code": "RATTISH_WORKSPACE_ERROR", "message": str(exc)},
    }
    if output_format == "json":
        _emit_json(payload)
    else:
        typer.echo(f"RATTISH_WORKSPACE_ERROR: {exc}", err=True)
