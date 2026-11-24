"""`Raticode rattish shortcuts`: the system-wide keys that run a workflow.

Shortcuts are added in the desktop app, which records the key (a workflow's menu >
**Add shortcut…**), and are listed in Settings > Devices. These commands list, approve
and remove them without a running backend; a running desktop app lets go of a removed key
within half a minute.

A shortcut runs only the workflow version you approved. ``approve`` prints what changed
and approves it; inside a Raticode agent turn it is refused, so an agent can't approve
its own edit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

app = typer.Typer(
    help=(
        "List, approve or remove workflow shortcuts: system-wide keys, added in the "
        "desktop app, that run a workflow from any app while Raticode is running."
    ),
    no_args_is_help=True,
)

_DATA_DIR_OPTION = typer.Option(
    None, "--data-dir", help="Override the app data directory that holds the shortcuts."
)
_FORMAT_OPTION = typer.Option("text", "--format", help="Output format: text or json.")
_APPROVAL_STATES = {
    "changed": "Changed since you approved it: the shortcut won't run it until you approve.",
    "unapproved": "Waiting for you to approve this workflow version.",
}


def _require_format(output_format: str) -> None:
    if output_format not in {"text", "json"}:
        typer.echo("Unsupported format. Use 'text' or 'json'.", err=True)
        raise typer.Exit(2)


def _fail(message: str, output_format: str, code: int = 1) -> typer.Exit:
    if output_format == "json":
        typer.echo(json.dumps({"ok": False, "error": message}, sort_keys=True))
    else:
        typer.echo(message, err=True)
    return typer.Exit(code)


def _key_label(binding: str) -> str:
    labels = {
        "Mod": "Ctrl",
        "Backquote": "`",
        "Minus": "-",
        "Equal": "=",
        "Comma": ",",
        "Period": ".",
        "Slash": "/",
        "Backslash": "\\",
        "Semicolon": ";",
        "Quote": "'",
        "BracketLeft": "[",
        "BracketRight": "]",
    }
    import sys

    if sys.platform == "darwin":
        labels.update({"Mod": "Command", "Meta": "Command", "Alt": "Option"})
    parts = []
    for part in binding.split("+"):
        label = labels.get(part, part)
        if label.startswith("Key") and len(label) == 4:
            label = label[3:]
        elif label.startswith("Digit") and len(label) == 6:
            label = label[5:]
        parts.append(label)
    return "+".join(parts)


@app.command("list")
def list_shortcuts(
    data_dir: Path | None = _DATA_DIR_OPTION,
    output_format: str = _FORMAT_OPTION,
) -> None:
    """List workflow shortcuts with their key and what pressing it does."""
    from gofer.rattish.workflow_shortcuts import ShortcutError
    from gofer.ui.workflow_shortcuts import list_payload
    from gofer.utils.paths import get_data_dir

    _require_format(output_format)
    base = data_dir or get_data_dir()
    try:
        payload = list_payload(base, details=True)
    except ShortcutError as exc:
        raise _fail(str(exc), output_format) from exc
    shortcuts: list[dict[str, Any]] = payload["shortcuts"]
    for item in shortcuts:
        item["key"] = _key_label(item["binding"])
    if output_format == "json":
        typer.echo(
            json.dumps({"ok": True, "shortcuts": shortcuts}, sort_keys=True, separators=(",", ":"))
        )
        return
    if not shortcuts:
        typer.echo(
            "No workflow shortcuts. Add one in the desktop app: a workflow's menu > Add shortcut…"
        )
        return
    for item in shortcuts:
        typer.echo(f"{item['id']}  {item['key']}  {item['workflowName']}")
        typer.echo(f"    {item['target']['summary']}")
        state = (item.get("approval") or {}).get("state")
        if state in _APPROVAL_STATES:
            typer.echo(
                f"    {_APPROVAL_STATES[state]} Review it with "
                f"`Raticode rattish shortcuts approve {item['id']}`."
            )


@app.command("remove")
def remove_shortcut(
    shortcut_id: str = typer.Argument(..., help="The shortcut's ID, from `shortcuts list`."),
    data_dir: Path | None = _DATA_DIR_OPTION,
    output_format: str = _FORMAT_OPTION,
) -> None:
    """Remove a workflow shortcut. The workflow itself is kept."""
    from gofer.rattish.workflow_shortcuts import ShortcutError, ShortcutNotFound
    from gofer.ui.workflow_shortcuts import remove_payload
    from gofer.utils.paths import get_data_dir

    _require_format(output_format)
    base = data_dir or get_data_dir()
    try:
        removed = remove_payload(base, shortcut_id.strip())["removed"]
    except ShortcutNotFound as exc:
        raise _fail(f"{exc} Run `Raticode rattish shortcuts list` to see them.", output_format)
    except ShortcutError as exc:
        raise _fail(str(exc), output_format) from exc
    if output_format == "json":
        typer.echo(json.dumps({"ok": True, "removed": removed}, sort_keys=True))
        return
    typer.echo(
        f"Removed shortcut {removed['id']} ({_key_label(removed['binding'])}). A running "
        "Raticode lets go of the key within half a minute."
    )


def _changes_text(changes: dict[str, Any]) -> str:
    from gofer.rattish.trigger_approval import unified_diff_text

    state = changes["state"]
    if state == "approved":
        return "This shortcut runs the workflow as it is now. Nothing to approve."
    if state == "missing":
        return "The workflow file is missing, so the shortcut can't run it until it's back."
    lines = [_APPROVAL_STATES.get(state, "")]
    if changes.get("approvedAt"):
        lines.append(f"Approved {changes['approvedAt']} from {changes.get('approvedBy')}.")
    body = unified_diff_text(changes)
    lines.append(body or "No file differs; the workflow uses a different set of files now.")
    if changes.get("unseen"):
        from gofer.rattish.trigger_approval import unseen_note

        lines.append(unseen_note(changes["unseen"]))
    return "\n".join(line for line in lines if line)


def _unseen_lines(unseen: list[dict[str, Any]]) -> list[str]:
    from gofer.rattish.trigger_approval import unseen_lines

    return unseen_lines(unseen)


def _confirm_unseen(files: list[dict[str, Any]], *, yes: bool, unseen: bool, fmt: str) -> bool:
    """Whether you approve files whose change wasn't shown: --unseen, or a second yes."""
    if not files or unseen:
        return unseen
    if yes or fmt != "text":
        return False
    from gofer.rattish.trigger_approval import unseen_heading

    heading, advice = unseen_heading(files)
    typer.echo(heading)
    for line in _unseen_lines(files):
        typer.echo(line)
    typer.echo(advice)
    return typer.confirm("Approve them without seeing all of their changes?", default=False)


@app.command("approve")
def approve(
    shortcut_id: str = typer.Argument(..., help="The shortcut's ID, from `shortcuts list`."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Approve without asking after the diff."),
    unseen: bool = typer.Option(
        False,
        "--unseen",
        help="Also approve files whose change can't be shown in full (a diff cut short, or a "
        "binary or very large file) and MCP servers whose package has no exact version. "
        "Without it you're asked again, or --yes alone refuses.",
    ),
    fingerprint: str | None = typer.Option(
        None,
        "--fingerprint",
        help="Approve only this version, as shown by `approve --format json`.",
    ),
    data_dir: Path | None = _DATA_DIR_OPTION,
    output_format: str = _FORMAT_OPTION,
) -> None:
    """Print what changed in the workflow, then approve this version for the shortcut.

    When a diff was cut short or a file can't be shown, this names those files and asks
    again; with --yes it also needs --unseen. Only you can approve: inside a Raticode
    agent turn this is refused (exit 5).
    """
    from gofer.cli.client import ExitCode, in_agent_session
    from gofer.rattish.workflow_shortcuts import (
        USER_ONLY,
        ApprovalConflict,
        ShortcutError,
        ShortcutNotFound,
        UnseenChanges,
        approve_shortcut,
        shortcut_changes,
    )
    from gofer.utils.paths import get_data_dir

    _require_format(output_format)
    if in_agent_session():
        if output_format == "json":
            typer.echo(
                json.dumps({"ok": False, "error": USER_ONLY, "code": "user_only"}, sort_keys=True)
            )
        else:
            typer.echo(USER_ONLY, err=True)
        raise typer.Exit(int(ExitCode.AUTH))
    base = data_dir or get_data_dir()
    shortcut_id = shortcut_id.strip()
    try:
        shown = shortcut_changes(base, shortcut_id)
    except ShortcutNotFound as exc:
        raise _fail(
            f"{exc} Run `Raticode rattish shortcuts list` to see them.", output_format
        ) from None
    except ShortcutError as exc:
        raise _fail(str(exc), output_format) from None
    if output_format == "text":
        typer.echo(_changes_text(shown))
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
        if not typer.confirm("Approve this version for the shortcut?", default=False):
            typer.echo("Not approved. The shortcut won't run the workflow until you approve.")
            raise typer.Exit(1)
    accept_unseen = _confirm_unseen(
        shown.get("unseen") or [], yes=yes, unseen=unseen, fmt=output_format
    )
    try:
        record = approve_shortcut(
            base,
            shortcut_id,
            actor="user",
            by="cli",
            expected_fingerprint=expected,
            accept_unseen=accept_unseen,
        )
    except UnseenChanges as exc:
        if output_format == "json":
            typer.echo(
                json.dumps(
                    {"ok": False, "error": str(exc), "unseen": exc.files, "changes": shown},
                    sort_keys=True,
                )
            )
        else:
            from gofer.rattish.trigger_approval import unseen_refusal

            typer.echo(unseen_refusal(exc.files), err=True)
        raise typer.Exit(2) from None
    except ApprovalConflict as exc:
        if output_format == "json":
            typer.echo(json.dumps({"ok": False, "error": str(exc), "conflict": True}))
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(int(ExitCode.CONFLICT)) from None
    except ShortcutError as exc:
        raise _fail(str(exc), output_format) from None
    if output_format == "json":
        typer.echo(
            json.dumps(
                {"ok": True, "approved": True, "shortcut": record.to_payload()}, sort_keys=True
            )
        )
    else:
        typer.echo(f"Approved. Shortcut {record.id} runs this version of the workflow from now on.")
