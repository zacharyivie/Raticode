from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from gofer.cli.commands import rattish_cache, rattish_secrets, rattish_triggers

if TYPE_CHECKING:
    from gofer.rattish.artifacts import CompiledArtifact, RattishArtifactError
    from gofer.rattish.diagnostics import RattishDiagnostic
    from gofer.rattish.formatter import RattishFormatError
    from gofer.rattish.run_service import RattishRunArtifactError, RattishRunResult
    from gofer.rattish.workspaces import RattishWorkspaceError

app = typer.Typer(
    help=(
        "Write, check, compile and run Rattish workflows. Commands marked (Backend) "
        "start, stop, inspect and approve runs through a running backend."
    ),
    no_args_is_help=True,
)
app.add_typer(rattish_triggers.app, name="triggers")
app.add_typer(rattish_secrets.app, name="secrets")
app.add_typer(rattish_cache.app, name="cache")


@app.command("docs")
def docs(
    output_format: str = typer.Option("text", "--format", help="Output format: text or json."),
) -> None:
    """Print paths to the installed Rattish documentation and node contracts."""
    from gofer.rattish.artifacts import RattishArtifactError, rattish_asset_root, rattish_docs_root

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
        "testSetSchema": str(asset_root / "schemas" / "test-set.schema.json"),
    }
    if output_format == "json":
        _emit_json(payload)
    else:
        typer.echo(docs_root)


@app.command("create")
def create(
    name: str | None = typer.Argument(
        None, help="Human-readable workflow name. Optional with --from."
    ),
    project: Path = typer.Option(
        Path("."), "--project", help="Repository project folder that will own the workflow."
    ),
    starter: str | None = typer.Option(
        None,
        "--from",
        help="Copy a starter workflow, its test set and sample files. See `rattish starters`.",
    ),
    output_format: str = typer.Option("text", "--format", help="Output format: text or json."),
    registry_dir: Path | None = typer.Option(
        None, "--registry-dir", help="Override the app directory used for workflow registration."
    ),
) -> None:
    """Create and register a workflow in PROJECT/.raticode/, empty or from a starter."""
    from gofer.rattish.artifacts import RattishArtifactError
    from gofer.rattish.diagnostics import RattishError
    from gofer.rattish.workspaces import RattishWorkspaceError, create_registered_workflow

    _require_format(output_format)
    if starter is None and not (name or "").strip():
        typer.echo("Give a workflow name, or a starter with --from.", err=True)
        raise typer.Exit(2)
    from_starter = None
    try:
        if starter is not None:
            from gofer.rattish.starters import create_workflow_from_starter, find_starter

            from_starter = find_starter(starter)
            workflow = create_workflow_from_starter(
                project, from_starter.starter_id, name, registry_dir=registry_dir
            )
        else:
            workflow = create_registered_workflow(project, name or "", registry_dir=registry_dir)
    except (RattishWorkspaceError, RattishArtifactError, RattishError) as exc:
        _emit_workspace_error("create", project, RattishWorkspaceError(str(exc)), output_format)
        raise typer.Exit(2)
    payload: dict[str, Any] = {"command": "create", "ok": True, "workflow": workflow.to_payload()}
    if from_starter is not None:
        payload["starter"] = from_starter.to_payload()
    if output_format == "json":
        _emit_json(payload)
        return
    typer.echo(f"Created {workflow.workflow_id} in {workflow.workflow_root}")
    if from_starter is not None:
        _echo_starter_next_steps(from_starter.to_payload(), workflow.entrypoint)


@app.command("starters")
def list_starters_command(
    output_format: str = typer.Option("text", "--format", help="Output format: text or json."),
) -> None:
    """List the starter workflows that `rattish create --from` can copy."""
    from gofer.rattish.starters import list_starters
    from gofer.rattish.workspaces import RattishWorkspaceError

    _require_format(output_format)
    try:
        starters = [item.to_payload() for item in list_starters()]
    except RattishWorkspaceError as exc:
        _emit_workspace_error("starters", Path("."), exc, output_format)
        raise typer.Exit(2)
    if output_format == "json":
        _emit_json({"command": "starters", "ok": True, "starters": starters})
        return
    if not starters:
        typer.echo("No starter workflows are installed.")
        return
    for item in starters:
        typer.echo(f"{item['id']}  {item['name']}")
        typer.echo(f"  {item['summary']}")


def _echo_starter_next_steps(starter: dict[str, Any], entrypoint: Path) -> None:
    if starter["providers"]:
        typer.echo(f"Uses the provider: {', '.join(starter['providers'])}.")
    for secret in starter["secrets"]:
        typer.echo(f"Reads the secret {secret} when used: Raticode rattish secrets set {secret}")
    if starter["trigger"]:
        command = starter["trigger"]["command"].replace("WORKFLOW_PATH", str(entrypoint))
        typer.echo(f"{starter['trigger']['description']} Add the trigger with:")
        typer.echo(f"  {command}")
    if starter["hasTests"]:
        typer.echo(f"Test it with: Raticode rattish test {entrypoint}")
    typer.echo(f"Guide: {starter['docsUrl']}")


@app.command("list")
def list_workflows(
    output_format: str = typer.Option("text", "--format", help="Output format: text or json."),
    project: Path | None = typer.Option(
        None,
        "--project",
        help=(
            "First find every workflow.rattish in this project folder, as opening it in the "
            "app does, then list only that project."
        ),
    ),
    registry_dir: Path | None = typer.Option(
        None, "--registry-dir", help="Override the app directory used for workflow registration."
    ),
) -> None:
    """List registered Rattish workflows grouped by repository project."""
    from gofer.rattish.workspaces import (
        RattishWorkspaceError,
        discover_project_workflows,
        list_registered_workflows,
    )

    _require_format(output_format)
    limited = False
    try:
        if project is not None:
            discovery = discover_project_workflows(project, registry_dir=registry_dir)
            limited = discovery.limited
            project_root = project.expanduser().resolve()
            found = {workflow.entrypoint for workflow in discovery.workflows}
            workflows = tuple(
                workflow
                for workflow in list_registered_workflows(registry_dir=registry_dir)
                if workflow.project_root == project_root or workflow.entrypoint in found
            )
        else:
            workflows = list_registered_workflows(registry_dir=registry_dir)
    except (RattishWorkspaceError, OSError) as exc:
        _emit_workspace_error(
            "list", project or Path("."), RattishWorkspaceError(str(exc)), output_format
        )
        raise typer.Exit(2)
    projects: dict[str, dict[str, Any]] = {}
    for workflow in workflows:
        key = str(workflow.project_root)
        group = projects.setdefault(
            key,
            {
                "root": key,
                "name": workflow.project_root.name or key,
                "workflows": [],
            },
        )
        group["workflows"].append(workflow.to_payload())
    payload: dict[str, Any] = {"command": "list", "ok": True, "projects": list(projects.values())}
    if project is not None:
        payload["discoveryLimited"] = limited
    if output_format == "json":
        _emit_json(payload)
        return
    if limited:
        typer.echo("Stopped looking for workflows at the folder limit.", err=True)
    if not projects:
        typer.echo("No registered Rattish workflows.")
        return
    for group in projects.values():
        typer.echo(f"{group['name']}  {group['root']}")
        for workflow in group["workflows"]:
            folder = "" if workflow["managed"] else f"  ({workflow['relativeFolder']})"
            typer.echo(f"  {workflow['id']}  {workflow['name']}{folder}")


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
    from gofer.rattish.diagnostics import RattishError
    from gofer.rattish.formatter import RattishFormatError, format_rattish_file

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
    from gofer.rattish.preflight import run_preflight

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
    execute_rattish_run(source, workflow_inputs, output_format=output_format, data_dir=data_dir)


@app.command("prune")
def prune(
    source: Path = typer.Argument(..., help="Path to a .rattish workflow file."),
    keep_last: int | None = typer.Option(
        None, "--keep-last", min=0, help="Always keep the newest N runs."
    ),
    keep_days: int | None = typer.Option(
        None, "--keep-days", min=0, help="Keep runs started in the last N days."
    ),
    keep_failed_days: int | None = typer.Option(
        None, "--keep-failed-days", min=0, help="Keep failed runs started in the last N days."
    ),
    apply: bool = typer.Option(
        False, "--apply", help="Delete the listed runs instead of previewing them."
    ),
    output_format: str = typer.Option("text", "--format", help="Output format: text or json."),
    data_dir: Path | None = typer.Option(
        None, "--data-dir", help="Override the app data directory used for runs and artifacts."
    ),
) -> None:
    """Preview or delete old runs of a Rattish workflow.

    Without a --keep option, the workflow's saved retention settings apply, the
    same ones Raticode uses to clean up after every run. Runs that are running,
    waiting on an approval or being resumed are never removed.
    """
    from gofer.rattish.run_retention import prune_run_records, saved_retention_settings
    from gofer.rattish.run_service import run_artifact_path
    from gofer.utils.paths import get_data_dir

    _require_format(output_format)
    artifact = _compile_or_exit(source, data_dir, output_format, "prune")
    workflow_id = str(artifact.ir["workflow"]["id"])
    base = data_dir or get_data_dir()
    if keep_last is None and keep_days is None and keep_failed_days is None:
        saved = saved_retention_settings(base, workflow_id)
        keep_last, keep_days, keep_failed_days = (
            saved["keepLast"],
            saved["keepDays"],
            saved["keepFailedDays"],
        )
    try:
        directory = run_artifact_path(artifact.source_path, "probe", data_dir=base).parent
        result = prune_run_records(
            base,
            workflow_id,
            directory,
            keep_last=keep_last,
            keep_days=keep_days,
            keep_failed_days=keep_failed_days,
            dry_run=not apply,
        )
    except OSError as exc:
        typer.echo(f"Could not prune runs of {workflow_id}: {exc}", err=True)
        raise typer.Exit(1)

    runs = [
        {
            "id": record.run_id,
            "startedAt": record.document.get("started_at"),
            "status": record.document.get("status"),
            "path": str(record.path),
        }
        for record in result.candidates
    ]
    if output_format == "json":
        _emit_json(
            {
                "command": "prune",
                "source": str(artifact.source_path),
                "workflowId": workflow_id,
                "dryRun": not apply,
                "keep": {
                    "keepLast": keep_last,
                    "keepDays": keep_days,
                    "keepFailedDays": keep_failed_days,
                },
                "runs": runs,
                "deleted": result.deleted,
                "skippedRuns": result.skipped,
            }
        )
        return
    action = "Removed" if apply else "Would remove"
    typer.echo(f"{action} {len(runs)} run(s) of {workflow_id}.")
    for run_item in runs:
        typer.echo(f"{run_item['id']}\t{run_item['startedAt'] or 'unknown'}\t{run_item['status']}")
    if result.skipped:
        typer.echo(f"Skipped {result.skipped} run record(s) that couldn't be read.", err=True)
    if runs and not apply:
        typer.echo("Run again with --apply to delete them.")


@app.command("test")
def test_workflow(
    source: Path = typer.Argument(..., help="Path to a .rattish workflow file."),
    tests: Path | None = typer.Option(
        None,
        "--tests",
        help="Test set file. Defaults to NAME.tests.json beside the workflow.",
    ),
    case: list[str] | None = typer.Option(
        None, "--case", help="Run only this case ID. Repeat for several cases."
    ),
    estimate_only: bool = typer.Option(
        False, "--estimate", help="Print the expected cost and run nothing."
    ),
    history: bool = typer.Option(
        False,
        "--history",
        help="List saved results and compare the newest with the last other revision.",
    ),
    max_cost: float | None = typer.Option(
        None,
        "--max-cost",
        min=0,
        help="Refuse to run when the expected cost in US dollars is over this or unknown.",
    ),
    skip_judge: bool = typer.Option(
        False, "--skip-judge", help="Skip model-judgment checks, so they cost nothing."
    ),
    output_format: str = typer.Option("text", "--format", help="Output format: text or json."),
    data_dir: Path | None = typer.Option(
        None, "--data-dir", help="Override the app data directory used for runs and results."
    ),
) -> None:
    """Run a workflow's test set: each case runs the workflow and its checks.

    Checks match text, compare a JSON field, run a command and test its exit code, or
    ask a model to judge the output. The expected cost is printed before anything
    runs. Results are saved with the workflow revision and compared with the last
    result of a different revision. Exit code 0 means every case passed, 1 that a
    case failed, 2 that the test set or workflow could not be used.
    """
    import asyncio

    from gofer.rattish.artifacts import RattishArtifactError
    from gofer.rattish.diagnostics import RattishError
    from gofer.rattish.test_sets import (
        WorkflowTestError,
        enforce_cost_limit,
        prepare_test_run,
        run_test_set,
        short_revision,
        with_comparison,
    )

    _require_format(output_format)
    if estimate_only and history:
        typer.echo("Use either --estimate or --history, not both.", err=True)
        raise typer.Exit(2)
    try:
        if history:
            _emit_test_history(source, data_dir, output_format)
            return
        prepared = prepare_test_run(
            source,
            tests_path=tests,
            case_ids=case or None,
            skip_judge=skip_judge,
            data_dir=data_dir,
        )
    except (WorkflowTestError, RattishArtifactError) as exc:
        _emit_test_error(source, str(exc), output_format)
        raise typer.Exit(2)
    except RattishError as exc:
        diagnostics = [item.to_json() for item in exc.diagnostics]
        _emit(_error_payload("test", source, diagnostics), output_format)
        raise typer.Exit(2)
    revision = prepared.ir["source"]["source_fingerprint"]
    base = {
        "command": "test",
        "source": str(prepared.source_path),
        "testSetPath": str(prepared.tests_path),
        "revision": revision,
        "estimate": prepared.estimate,
    }
    if output_format == "text":
        typer.echo(
            f"{prepared.ir['workflow']['name']}  revision {short_revision(revision)}  "
            f"{len(prepared.cases)} case{'' if len(prepared.cases) == 1 else 's'}"
        )
        typer.echo(f"Expected cost: {prepared.estimate['summary']}")
    if estimate_only:
        if output_format == "json":
            _emit_json({**base, "ok": True})
        return
    try:
        enforce_cost_limit(prepared.estimate, max_cost)
    except WorkflowTestError as exc:
        _emit_test_error(source, str(exc), output_format, base)
        raise typer.Exit(2)

    def progress(document: dict[str, Any]) -> None:
        if output_format != "text":
            return
        for item in document["cases"]:
            key = str(item["id"])
            if item["status"] in {"pending", "running"} or key in reported:
                continue
            reported.add(key)
            _echo_test_case(item)

    reported: set[str] = set()
    try:
        document = asyncio.run(run_test_set(prepared, on_progress=progress))
    except WorkflowTestError as exc:
        _emit_test_error(source, str(exc), output_format, base)
        raise typer.Exit(2)
    result = with_comparison(document, prepared.history)
    result_path = prepared.directory / f"{document['id']}.json"
    if output_format == "json":
        _emit_json(
            {
                **base,
                "ok": document["status"] == "passed",
                "resultPath": str(result_path),
                "result": result,
            }
        )
    else:
        _echo_test_totals(document)
        typer.echo(f"Result: {result_path}")
        _echo_test_comparison(result.get("comparison"))
    if document["status"] != "passed":
        raise typer.Exit(1)


def _emit_test_history(source: Path, data_dir: Path | None, output_format: str) -> None:
    from gofer.rattish.artifacts import compile_rattish_file
    from gofer.rattish.test_sets import (
        list_test_results,
        result_summary,
        results_directory,
        short_revision,
        with_comparison,
        workflow_tests_file,
    )
    from gofer.utils.paths import get_data_dir

    resolved_data_dir = (data_dir or get_data_dir()).expanduser().resolve()
    artifact = compile_rattish_file(source, data_dir=resolved_data_dir)
    directory = results_directory(
        artifact.source_path, str(artifact.ir["workflow"]["id"]), resolved_data_dir
    )
    documents = list_test_results(directory)
    latest = with_comparison(documents[0], documents) if documents else None
    if output_format == "json":
        _emit_json(
            {
                "command": "test",
                "ok": True,
                "source": str(artifact.source_path),
                "testSetPath": str(workflow_tests_file(artifact.source_path)),
                "revision": artifact.ir["source"]["source_fingerprint"],
                "results": [result_summary(item) for item in documents],
                "latest": latest,
            }
        )
        return
    if not documents:
        typer.echo(f"{artifact.source_path}: no saved test results.")
        return
    current = artifact.ir["source"]["source_fingerprint"]
    for item in documents:
        totals = item.get("totals") or {}
        marker = " (current)" if item.get("revision") == current else ""
        typer.echo(
            f"{item['id']}  {item.get('status')}  revision {short_revision(item.get('revision'))}"
            f"{marker}  {totals.get('passed', 0)}/{totals.get('cases', 0)} passed"
        )
    if latest is not None:
        _echo_test_comparison(latest.get("comparison"))


def _echo_test_case(item: dict[str, Any]) -> None:
    label = {"passed": "PASS", "failed": "FAIL", "error": "ERROR", "skipped": "SKIP"}.get(
        str(item["status"]), str(item["status"]).upper()
    )
    seconds = (item.get("durationMs") or 0) / 1000
    name = f"  {item['name']}" if item.get("name") and item["name"] != item["id"] else ""
    typer.echo(f"{label:<5} {item['id']}{name}  ({seconds:.1f}s)")
    if item["status"] == "error" and item.get("message"):
        typer.echo(f"      {item['message']}")
    for check in item.get("checks") or []:
        if check["status"] in {"failed", "error"}:
            typer.echo(f"      x {check['label']}: {check['message']}")
        elif check["status"] == "skipped":
            typer.echo(f"      - {check['label']}: {check['message']}")


def _echo_test_totals(document: dict[str, Any]) -> None:
    from gofer.rattish.test_sets import format_usd

    totals = document["totals"]
    parts = [f"{totals['passed']} passed", f"{totals['failed']} failed"]
    if totals["errors"]:
        parts.append(f"{totals['errors']} could not run")
    if totals["skipped"]:
        parts.append(f"{totals['skipped']} skipped")
    calls = totals["modelCalls"] + totals["judgeCalls"]
    cost = format_usd(totals["costUsd"])
    if not totals["costComplete"]:
        cost = f"at least {cost} (some providers report no cost)"
    typer.echo(f"{document['status'].upper()}: {', '.join(parts)}. {calls} model calls, {cost}.")


def _echo_test_comparison(comparison: dict[str, Any] | None) -> None:
    from gofer.rattish.test_sets import short_revision

    if not comparison:
        return
    summary = comparison["summary"]
    changes = [
        f"{summary[key]} {key}"
        for key in ("broke", "fixed", "changed", "new", "removed")
        if summary[key]
    ]
    typer.echo(
        f"Compared with revision {short_revision(comparison['previousRevision'])} "
        f"({comparison['previousStartedAt']}): {', '.join(changes) or 'no case changed'}."
    )
    for item in comparison["cases"]:
        if item["change"] != "same":
            typer.echo(f"  {item['change']:<8} {item['id']}  {item['before']} -> {item['after']}")


def _emit_test_error(
    source: Path,
    message: str,
    output_format: str,
    base: dict[str, Any] | None = None,
) -> None:
    if output_format == "json":
        payload = base or {"command": "test", "source": str(source.expanduser().resolve())}
        _emit_json({**payload, "ok": False, "error": message})
    else:
        typer.echo(message, err=True)


def execute_rattish_run(
    source: Path,
    workflow_inputs: dict[str, Any],
    *,
    output_format: str = "text",
    data_dir: Path | None = None,
) -> None:
    """Run one Rattish file for a CLI command and exit with its run status."""
    import asyncio

    from gofer.cli.client import in_agent_session
    from gofer.rattish.artifacts import RattishArtifactError
    from gofer.rattish.diagnostics import RattishError
    from gofer.rattish.run_service import RattishRunArtifactError, run_rattish_file

    try:
        result = asyncio.run(
            run_rattish_file(
                source,
                workflow_inputs=workflow_inputs,
                data_dir=data_dir,
                started_by="rem" if in_agent_session() else "manual",
            )
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


def execute_rattish_resume(
    source: Path,
    run_id: str,
    *,
    output_format: str = "text",
    data_dir: Path | None = None,
) -> None:
    """Resume a saved Rattish run as a new run and exit with that run's status."""
    import asyncio

    from gofer.rattish.artifacts import RattishArtifactError
    from gofer.rattish.diagnostics import RattishError
    from gofer.rattish.run_service import (
        RattishResumeRefused,
        RattishRunArtifactError,
        effective_run_status,
        resume_rattish_run,
        run_artifact_path,
    )

    try:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id) is None:
            raise RattishResumeRefused(f"Invalid run ID {run_id!r}.")
        path = run_artifact_path(source, run_id.removesuffix(".json"), data_dir=data_dir)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise RattishResumeRefused(f"Run {run_id!r} was not found.") from None
        if isinstance(document, dict):
            document["status"] = effective_run_status(document)
        from gofer.cli.client import in_agent_session

        result = asyncio.run(
            resume_rattish_run(
                source,
                document,
                data_dir=data_dir,
                started_by="rem" if in_agent_session() else "manual",
            )
        )
    except RattishResumeRefused as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    except RattishError as exc:
        diagnostics = [item.to_json() for item in exc.diagnostics]
        _emit(_error_payload("run", source, diagnostics), output_format)
        raise typer.Exit(1)
    except (RattishArtifactError, RattishRunArtifactError) as exc:
        diagnostic = _artifact_diagnostic(source, exc).to_json()
        _emit(_error_payload("run", source, [diagnostic]), output_format)
        raise typer.Exit(2)

    _emit_run(result, output_format)
    if not result.ok:
        raise typer.Exit(1)


def _compile_or_exit(
    source: Path,
    data_dir: Path | None,
    output_format: str,
    command: str,
) -> CompiledArtifact:
    from gofer.rattish.artifacts import RattishArtifactError, compile_rattish_file
    from gofer.rattish.diagnostics import RattishError

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
    from gofer.rattish.diagnostics import RattishDiagnostic, SourcePosition, SourceSpan

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
    from gofer.rattish.diagnostics import RattishDiagnostic, SourcePosition, SourceSpan

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
    usage = result.document.get("usage")
    if isinstance(usage, dict) and (usage.get("calls") or usage.get("cached_results")):
        from gofer.rattish.run_usage import usage_line

        typer.echo(f"Usage: {usage_line(usage)}")
    records = result.document.get("runs") or []
    for record in records:
        if record.get("cached_at"):
            typer.echo(
                f"{record['node_id']}: cached result saved {record['cached_at']} "
                "(no model call, 0 tokens, $0)"
            )
    switched = [(record, item) for record in records for item in record.get("fallbacks") or []]
    if switched:
        from gofer.rattish.provider_fallback import provider_label, switch_message

        for record, item in switched:
            typer.echo(f"{record['node_id']}: {switch_message(item)}")
        for record in records:
            if record.get("fallbacks") and record.get("outcome") == "success":
                typer.echo(f"{record['node_id']}: answered by {provider_label(record['provider'])}")
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
