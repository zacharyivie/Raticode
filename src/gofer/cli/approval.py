"""The "approve this version" flow shared by `rattish triggers approve` and
`rattish shortcuts approve`.

Both commands print what changed in the workflow and approve that version. Only the user
may approve: inside a Raticode agent turn the command is refused (exit 5) before anything
is read.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import typer

from gofer.cli.output import confirm_unseen


def changes_text(
    changes: dict[str, Any], *, noun: str, states: dict[str, str], missing: str
) -> str:
    """A workflow's waiting changes as text: its state, who approved it, and the diff.

    ``changedDuringRun`` (triggers only) adds a line naming the file that changed while a
    triggered run used it.
    """
    from gofer.rattish.trigger_approval import unified_diff_text, unseen_note

    state = changes["state"]
    if state == "approved":
        return f"This {noun} runs the workflow as it is now. Nothing to approve."
    if state == "missing":
        return missing
    lines = [states.get(state, "")]
    if changes.get("approvedAt"):
        lines.append(f"Approved {changes['approvedAt']} from {changes.get('approvedBy')}.")
    marked = changes.get("changedDuringRun")
    if marked:
        lines.append(
            f"{marked.get('path')} changed during a triggered run, so the step that uses it "
            "wasn't run."
        )
    body = unified_diff_text(changes)
    if marked and not body:
        body = "No file differs from the approved version now."
    lines.append(body or "No file differs; the workflow uses a different set of files now.")
    if changes.get("unseen"):
        lines.append(unseen_note(changes["unseen"]))
    return "\n".join(line for line in lines if line)


def approve_version(
    *,
    noun: str,
    output_format: str,
    yes: bool,
    unseen: bool,
    fingerprint: str | None,
    user_only: str,
    show: Callable[[], dict[str, Any]],
    approve: Callable[[str, bool], Any],
    errors: type[Exception],
    error_exit: Callable[[Exception], typer.Exit],
    approval_conflict: type[Exception],
    unseen_changes: type[Exception],
    text: Callable[[dict[str, Any]], str],
    prompt: str,
    declined: str,
    approved: Callable[[Any], str],
    payload: Callable[[Any], dict[str, Any]],
) -> None:
    """Print what changed, then approve this version of the workflow for one ``noun``.

    ``show`` reads the changes; ``approve(expected_fingerprint, accept_unseen)`` approves
    them and returns the record, which ``payload`` turns into the JSON ``noun`` value and
    ``approved`` into the success sentence. ``errors`` raised by either go to
    ``error_exit``. Exit codes: 5 in an agent turn, 1 when you say no, 2 for --format json
    without --yes or for files you haven't seen, CONFLICT when the version changed.
    """
    from gofer.cli.client import ExitCode, in_agent_session
    from gofer.rattish.trigger_approval import unseen_refusal

    if in_agent_session():
        if output_format == "json":
            typer.echo(
                json.dumps({"ok": False, "error": user_only, "code": "user_only"}, sort_keys=True)
            )
        else:
            typer.echo(user_only, err=True)
        raise typer.Exit(int(ExitCode.AUTH))
    try:
        shown = show()
    except errors as exc:
        raise error_exit(exc) from None
    if output_format == "text":
        typer.echo(text(shown))
    if shown["state"] == "approved" and fingerprint is None:
        if output_format == "json":
            typer.echo(
                json.dumps({"ok": True, "approved": False, "changes": shown}, sort_keys=True)
            )
        return
    expected = fingerprint or shown["fingerprint"]
    if not yes:
        if output_format == "json":
            typer.echo(
                json.dumps(
                    {
                        "ok": False,
                        "error": "Add --yes to approve without a prompt.",
                        "changes": shown,
                    },
                    sort_keys=True,
                )
            )
            raise typer.Exit(2)
        if not typer.confirm(prompt, default=False):
            typer.echo(declined)
            raise typer.Exit(1)
    accept_unseen = confirm_unseen(
        shown.get("unseen") or [], yes=yes, unseen=unseen, fmt=output_format
    )
    try:
        record = approve(expected, accept_unseen)
    except unseen_changes as exc:
        files = getattr(exc, "files", [])
        if output_format == "json":
            typer.echo(
                json.dumps(
                    {"ok": False, "error": str(exc), "unseen": files, "changes": shown},
                    sort_keys=True,
                )
            )
        else:
            typer.echo(unseen_refusal(files), err=True)
        raise typer.Exit(2) from None
    except approval_conflict as exc:
        if output_format == "json":
            typer.echo(json.dumps({"ok": False, "error": str(exc), "conflict": True}))
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(int(ExitCode.CONFLICT)) from None
    except errors as exc:
        raise error_exit(exc) from None
    if output_format == "json":
        typer.echo(
            json.dumps({"ok": True, "approved": True, noun: payload(record)}, sort_keys=True)
        )
    else:
        typer.echo(approved(record))
