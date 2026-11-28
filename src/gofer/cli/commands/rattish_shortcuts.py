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

from gofer.cli.output import fail, require_format

app = typer.Typer(
    help=(
        "List, approve or remove workflow shortcuts: system-wide keys, added in the "
        "desktop app, that run a workflow from any app while Raticode is running."
    ),
    no_args_is_help=True,
)

_DATA_DIR_OPTION = typer.Option(
    None, "--data-dir", help="Override the data folder that holds the shortcuts."
)
_FORMAT_OPTION = typer.Option("text", "--format", help="Output format: text or json.")
_APPROVAL_STATES = {
    "changed": "Changed since you approved it: the shortcut won't run it until you approve.",
    "unapproved": "Waiting for you to approve this workflow version.",
}


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

    require_format(output_format)
    base = data_dir or get_data_dir()
    try:
        payload = list_payload(base, details=True)
    except ShortcutError as exc:
        raise fail(str(exc), output_format) from exc
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

    require_format(output_format)
    base = data_dir or get_data_dir()
    try:
        removed = remove_payload(base, shortcut_id.strip())["removed"]
    except ShortcutNotFound as exc:
        raise fail(f"{exc} Run `Raticode rattish shortcuts list` to see them.", output_format)
    except ShortcutError as exc:
        raise fail(str(exc), output_format) from exc
    if output_format == "json":
        typer.echo(json.dumps({"ok": True, "removed": removed}, sort_keys=True))
        return
    typer.echo(
        f"Removed shortcut {removed['id']} ({_key_label(removed['binding'])}). A running "
        "Raticode lets go of the key within half a minute."
    )


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
    from gofer.cli.approval import approve_version, changes_text
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

    require_format(output_format)
    base = data_dir or get_data_dir()
    shortcut_id = shortcut_id.strip()

    def error_exit(exc: Exception) -> typer.Exit:
        if isinstance(exc, ShortcutNotFound):
            return fail(f"{exc} Run `Raticode rattish shortcuts list` to see them.", output_format)
        return fail(str(exc), output_format)

    approve_version(
        noun="shortcut",
        output_format=output_format,
        yes=yes,
        unseen=unseen,
        fingerprint=fingerprint,
        user_only=USER_ONLY,
        show=lambda: shortcut_changes(base, shortcut_id),
        approve=lambda expected, accept_unseen: approve_shortcut(
            base,
            shortcut_id,
            actor="user",
            by="cli",
            expected_fingerprint=expected,
            accept_unseen=accept_unseen,
        ),
        errors=ShortcutError,
        error_exit=error_exit,
        approval_conflict=ApprovalConflict,
        unseen_changes=UnseenChanges,
        text=lambda shown: changes_text(
            shown,
            noun="shortcut",
            states=_APPROVAL_STATES,
            missing="The workflow file is missing, so the shortcut can't run it until it's back.",
        ),
        prompt="Approve this version for the shortcut?",
        declined="Not approved. The shortcut won't run the workflow until you approve.",
        approved=lambda record: (
            f"Approved. Shortcut {record.id} runs this version of the workflow from now on."
        ),
        payload=lambda record: record.to_payload(),
    )
