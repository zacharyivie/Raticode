"""`Raticode ui second-brain`: the Second Brain MCP server Rem starts for each provider turn.

`gofer.cli.main` sends this command here before it builds the Typer app, so the server
starts without loading the rest of the CLI. Anything this short path can't parse, and
`--help`, goes through the full Typer command instead, which prints the usual help and errors.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def run_second_brain(
    root: Path,
    report_format: str,
    report_theme: str,
    library_data_dir: Path | None,
    brain_ids: list[str] | None,
    context: dict[str, str],
    memory_folder: bool = False,
    report_theme_file: Path | None = None,
    task_report_tool: bool = False,
) -> None:
    from gofer.ui.report_themes import read_theme_file
    from gofer.ui.second_brain import serve_registered_brains, serve_second_brain

    # The turn's report theme, for the rules tool. Without one, report_theme names the
    # Second Brain's own theme.
    theme_config = read_theme_file(report_theme_file) if report_theme_file is not None else None
    if library_data_dir is not None and brain_ids:
        serve_registered_brains(
            library_data_dir,
            brain_ids,
            report_format,
            report_theme,
            context,
            # --root is the folder the user approved by path; its brain needs no
            # cloud permission.
            memory_folder=root if memory_folder else None,
            theme_config=theme_config,
            task_report_tool=task_report_tool,
        )
        return
    serve_second_brain(
        root,
        report_format,
        report_theme,
        theme_config=theme_config,
        task_report_tool=task_report_tool,
    )


class _NotParsed(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise _NotParsed(message)


def _parser() -> _Parser:
    # Keep these in step with second_brain() in gofer.cli.main; a test compares them.
    parser = _Parser(prog="Raticode ui second-brain", add_help=False, allow_abbrev=False)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--report-format", default="md")
    parser.add_argument("--report-theme", default="auto")
    parser.add_argument("--library-data-dir", type=Path, default=None)
    parser.add_argument("--brain-id", action="append", default=None)
    parser.add_argument("--thread-id", default="")
    parser.add_argument("--project-root", default="")
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--memory-folder", action="store_true")
    parser.add_argument("--report-theme-file", type=Path, default=None)
    parser.add_argument("--task-report-tool", action="store_true")
    return parser


def option_names() -> set[str]:
    """Every long option the short path accepts."""
    return {
        option
        for action in _parser()._actions
        for option in action.option_strings
        if option.startswith("--")
    }


def fast_main(argv: list[str]) -> int | None:
    """Serve straight away, or return None so the caller runs the full Typer command."""
    if any(arg in ("-h", "--help") for arg in argv):
        return None
    try:
        args = _parser().parse_args(argv)
    except _NotParsed:
        return None
    run_second_brain(
        args.root,
        args.report_format,
        args.report_theme,
        args.library_data_dir,
        args.brain_id,
        {
            "threadId": args.thread_id,
            "projectRoot": args.project_root,
            "provider": args.provider,
            "model": args.model,
        },
        args.memory_folder,
        args.report_theme_file,
        args.task_report_tool,
    )
    return 0
