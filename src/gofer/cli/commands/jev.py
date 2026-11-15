"""``Raticode provider jev``: the Jev classifier's key and status, through the backend.

Jev is a classification provider, not a coding provider, so it has its own group. The
key is read from a hidden prompt or standard input and sent to the backend in the
request body. It is never a command-line argument (other local accounts can read a
process's arguments) and the backend keeps it in the OS keychain.
"""

from __future__ import annotations

import sys
from typing import Any

import typer

from gofer.cli import client_options as opts
from gofer.cli.client import CliError, Output
from gofer.cli.commands import _resources as res
from gofer.cli.commands._backend import require_user
from gofer.cli.commands._resources import Backend, finish, session

app = typer.Typer(
    help=(
        "Jev, TypeSafe's yes/no and multiple-choice classifier used by jev workflow nodes. "
        "It isn't a coding provider."
    ),
    no_args_is_help=True,
)


def _summary(data: dict[str, Any]) -> str:
    if data.get("keySource") == "secrets folder":
        key = "read from the secrets folder (jev-key)"
    else:
        key = "saved" if data.get("configured") else "not saved"
    lines = [
        f"Key: {key}",
        f"Jev: {'on' if data.get('enabled') else 'off'}",
        f"Default node model: {data.get('defaultModel')}",
    ]
    if data.get("checkedAt"):
        result = data.get("checkError") or "key accepted"
        lines.append(f"Last check: {data['checkedAt']} ({result})")
    models = data.get("models") or []
    if models:
        lines.append("Models: " + ", ".join(str(model) for model in models))
    return "\n".join(lines)


@app.command("status")
def status(
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show whether a Jev key is saved, whether Jev is on, and the last key check."""
    current = session("provider jev status", output, endpoint, token_file, data_dir)
    finish(current, lambda b, _o: b.request("GET", "/classifiers/jev"), _summary)


@app.command("check")
def check(
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Check the saved key with Jev and refresh its model list. Makes one Jev call."""
    current = session("provider jev check", output, endpoint, token_file, data_dir)

    def run(backend: Backend, _out: Output) -> Any:
        require_user("check the Jev key")
        return backend.request("POST", "/classifiers/jev/check", {}, timeout=60)

    finish(current, run, _summary)


def _read_key(from_stdin: bool) -> str:
    if from_stdin or not sys.stdin.isatty():
        return sys.stdin.readline().strip()
    return str(typer.prompt("Jev API key", hide_input=True)).strip()


@app.command("set-key")
def set_key(
    from_stdin: bool = typer.Option(
        False, "--stdin", help="Read the key from standard input instead of a hidden prompt."
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Save a Jev API key in the system keychain. Prompts without echoing it."""
    current = session("provider jev set-key", output, endpoint, token_file, data_dir)
    try:
        # Refuse before prompting, so an agent turn never reads a key from its input.
        require_user("save a Jev key")
    except CliError as exc:
        Output(current.command, current.output).error(exc)
        raise typer.Exit(int(exc.exit_code)) from None
    key = _read_key(from_stdin)
    if not key:
        res.invalid(current, "No key entered.")

    def save(backend: Backend, _out: Output) -> Any:
        return backend.request("POST", "/classifiers/jev/key", {"key": key})

    finish(current, save, lambda _d: "Saved the Jev key in the system keychain.")


@app.command("remove-key")
def remove_key(
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Delete the saved Jev key from the system keychain."""
    current = session("provider jev remove-key", output, endpoint, token_file, data_dir)

    def remove(backend: Backend, _out: Output) -> Any:
        require_user("remove the Jev key")
        return backend.request("DELETE", "/classifiers/jev/key")

    finish(current, remove, lambda _d: "Removed the Jev key.")


def _switch(command: str, enabled: bool) -> Any:
    def run(
        output: opts.Output = opts.OutputFormat.json,
        endpoint: opts.EndpointUrl = None,
        token_file: opts.TokenFile = None,
        data_dir: opts.DataDir = None,
    ) -> None:
        current = session(f"provider jev {command}", output, endpoint, token_file, data_dir)

        def save(backend: Backend, _out: Output) -> Any:
            require_user("turn Jev on or off")
            return backend.request("POST", "/classifiers/jev/settings", {"enabled": enabled})

        finish(current, save, lambda _d: f"Jev is {'on' if enabled else 'off'}.")

    run.__doc__ = (
        "Let jev workflow nodes call Jev with the saved key."
        if enabled
        else "Stop every jev workflow node from calling Jev, keeping the saved key."
    )
    return run


app.command("enable")(_switch("enable", True))
app.command("disable")(_switch("disable", False))
