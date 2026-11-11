"""Helpers the Rem, ticket and self-work commands share on top of :mod:`gofer.cli.client`.

These commands are thin wrappers over the backend's HTTP API, the same one the desktop
window and phone use, so the backend stays the only writer of Rem's stores and every
rule the app enforces (revisions, approvals, who may finish a ticket) applies unchanged.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from gofer.cli import client_options as opts
from gofer.cli.client import (
    AGENT_SESSION_ENVS,
    ApiClient,
    CliError,
    CommandResult,
    ExitCode,
    Output,
    in_agent_session,
    new_request_id,
)

# The client core's agent-session markers: what Raticode sets for every provider turn it
# starts (``gofer.utils.process`` and ``gofer.core.command_slots``).
AGENT_ENV = AGENT_SESSION_ENVS

Body = Callable[[ApiClient, Output], CommandResult | None]


def run(
    command: str,
    output: opts.OutputFormat,
    connection: tuple[str | None, Path | None, Path | None],
    body: Body,
    *,
    request_id: str | None = None,
) -> None:
    """Connect to the backend, run ``body`` and print its envelope; exits with its code."""

    def wrapped(out: Output) -> CommandResult | None:
        try:
            return body(opts.connect(*connection), out)
        except CliError as exc:
            exc.request_id = exc.request_id or request_id
            raise

    opts.run_command(command, output, wrapped, request_id=request_id)


def request_id(value: str | None) -> str:
    """``--request-id`` as given (``auto`` makes one), or a new one: these endpoints need it."""
    return opts.request_id_value(value) or new_request_id()


def invalid(message: str) -> CliError:
    return CliError(message, code="invalid_input", exit_code=ExitCode.USAGE)


def segment(value: str) -> str:
    """An ID as one URL path segment."""
    if not value or "/" in value:
        raise invalid("Give an ID.")
    return quote(value, safe="")


def agent_session() -> bool:
    """Whether this command runs inside a provider turn Raticode started for Rem or a worker."""
    return in_agent_session()


def require_user(action: str) -> None:
    """Refuse decisions only the user may make when an agent is running the command.

    The backend treats every API caller holding the token as the user, so this keeps Rem
    and its workers from approving their own proposals, marking their own tickets done
    or widening their own reach through the CLI. It is a guard, not a boundary.
    """
    if agent_session():
        raise CliError(
            f"Only you can {action}. This command is running inside a Raticode agent turn; "
            "run it from your own terminal or use the app.",
            code="user_only",
            exit_code=ExitCode.AUTH,
        )


def read_body(text: str | None, file: Path | None, what: str) -> str:
    """Text from an argument, a file, or stdin when the argument is ``-``."""
    if file is not None:
        if text:
            raise invalid(f"Give the {what} as text or with --file, not both.")
        try:
            return file.read_text(encoding="utf-8")
        except OSError as exc:
            raise invalid(f"Couldn't read {file}: {exc.strerror}") from None
    if text == "-":
        return sys.stdin.read()
    return text or ""


def read_json_file(path: Path) -> Any:
    try:
        raw = sys.stdin.read() if str(path) == "-" else path.read_text(encoding="utf-8")
        return json.loads(raw)
    except OSError as exc:
        raise invalid(f"Couldn't read {path}: {exc.strerror}") from None
    except ValueError as exc:
        raise invalid(f"{path} isn't valid JSON: {exc}") from None
