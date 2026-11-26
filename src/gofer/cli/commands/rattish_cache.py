"""`Raticode rattish cache`: the saved answers of nodes with ``cache: DURATION``.

Each workflow keeps its own result cache under the data folder. These commands show how
much one holds and clear it, the same as Workflow settings > Result cache.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from gofer.cli.output import fail

app = typer.Typer(
    help=(
        "Show or clear the result cache: saved answers of common-llm-task and jev nodes "
        "with cache: DURATION. Each workflow has its own."
    ),
    no_args_is_help=True,
)

_SOURCE_ARGUMENT = typer.Argument(
    None, help="Path to a .rattish workflow file. Or name the workflow with --workflow-id."
)
_WORKFLOW_ID_OPTION = typer.Option(
    None, "--workflow-id", help="The workflow's ID, instead of a source path."
)
_DATA_DIR_OPTION = typer.Option(
    None, "--data-dir", help="Override the data folder that holds the cache."
)
_FORMAT_OPTION = typer.Option("text", "--format", help="Output format: text or json.")


def _require_format(output_format: str) -> None:
    if output_format not in {"text", "json"}:
        typer.echo("Unsupported format. Use 'text' or 'json'.", err=True)
        raise typer.Exit(2)


def _resolve_workflow_id(
    source: Path | None, workflow_id: str | None, data_dir: Path, output_format: str
) -> str:
    from gofer.rattish.result_cache import is_cacheable_workflow_id

    if (source is None) == (workflow_id is None):
        raise fail("Give a .rattish source path or --workflow-id, not both.", output_format, 2)
    if workflow_id is None:
        from gofer.rattish.artifacts import RattishArtifactError, compile_rattish_file
        from gofer.rattish.diagnostics import RattishError

        assert source is not None
        try:
            workflow_id = str(compile_rattish_file(source, data_dir=data_dir).ir["workflow"]["id"])
        except (RattishError, RattishArtifactError, OSError) as exc:
            raise fail(
                f"Could not compile {source} to find its workflow ID: {exc}. "
                "Use --workflow-id instead.",
                output_format,
            ) from exc
    if not is_cacheable_workflow_id(workflow_id):
        raise fail(f"Invalid workflow ID {workflow_id!r}.", output_format, 2)
    return workflow_id


def _size(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value / (1024 * 1024):.1f} MB"


def _emit(payload: dict[str, Any], output_format: str, text: str) -> None:
    if output_format == "json":
        typer.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        typer.echo(text)


@app.command("show")
def show(
    source: Path | None = _SOURCE_ARGUMENT,
    workflow_id: str | None = _WORKFLOW_ID_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
    output_format: str = _FORMAT_OPTION,
) -> None:
    """Show how many saved answers a workflow's result cache holds and their size."""
    from gofer.rattish.result_cache import ResultCache
    from gofer.utils.paths import get_data_dir

    _require_format(output_format)
    base = data_dir or get_data_dir()
    safe_id = _resolve_workflow_id(source, workflow_id, base, output_format)
    stats = ResultCache(base).stats(safe_id)
    _emit(
        {
            "ok": True,
            "workflowId": safe_id,
            "entries": stats.entries,
            "bytes": stats.bytes,
            "maxBytes": stats.max_bytes,
        },
        output_format,
        f"{safe_id}: {stats.entries} saved answer(s), {_size(stats.bytes)} "
        f"of {_size(stats.max_bytes)}.",
    )


@app.command("clear")
def clear(
    source: Path | None = _SOURCE_ARGUMENT,
    workflow_id: str | None = _WORKFLOW_ID_OPTION,
    all_workflows: bool = typer.Option(False, "--all", help="Clear every workflow's result cache."),
    data_dir: Path | None = _DATA_DIR_OPTION,
    output_format: str = _FORMAT_OPTION,
) -> None:
    """Remove a workflow's saved answers, so its cached nodes call their model again."""
    from gofer.rattish.result_cache import ResultCache
    from gofer.utils.paths import get_data_dir

    _require_format(output_format)
    base = data_dir or get_data_dir()
    cache = ResultCache(base)
    try:
        if all_workflows:
            if source is not None or workflow_id is not None:
                raise fail(
                    "--all clears every workflow; leave out the source and ID.", output_format, 2
                )
            removed = cache.clear_all()
            _emit(
                {"ok": True, "workflowId": None, "removed": removed},
                output_format,
                f"Removed {removed} saved answer(s) from every workflow's result cache.",
            )
            return
        safe_id = _resolve_workflow_id(source, workflow_id, base, output_format)
        removed = cache.clear(safe_id)
    except OSError as exc:
        raise fail(f"Could not clear the result cache: {exc}", output_format) from exc
    _emit(
        {"ok": True, "workflowId": safe_id, "removed": removed},
        output_format,
        f"Removed {removed} saved answer(s) from {safe_id}'s result cache.",
    )
