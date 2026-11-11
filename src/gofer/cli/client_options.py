"""Reusable Typer options for commands built on :mod:`gofer.cli.client`.

Use them as annotations so every backend-client command spells its flags the same
way::

    from gofer.cli import client_options as opts

    def tasks_list(
        output: opts.Output = opts.OutputFormat.json,
        endpoint: opts.EndpointUrl = None,
        token_file: opts.TokenFile = None,
        data_dir: opts.DataDir = None,
    ) -> None:
        def body(out):
            client = opts.connect(endpoint, token_file, data_dir)
            return opts.CommandResult(data=client.get("/rem/tasks"))

        opts.run_command("rem tasks list", output, body)

There is deliberately no ``--token`` option: tokens never go in argv.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from gofer.cli.client import (
    DEFAULT_REQUEST_TIMEOUT,
    ApiClient,
    CliError,
    CommandResult,
    ExitCode,
    OutputFormat,
    discover_endpoint,
    new_request_id,
    parse_duration,
    run_command,
)

__all__ = [
    "CommandResult",
    "DataDir",
    "EndpointUrl",
    "ExpectedRevision",
    "Interval",
    "NonInteractive",
    "Output",
    "OutputFormat",
    "RequestId",
    "Timeout",
    "TokenFile",
    "Yes",
    "connect",
    "expected_revision_value",
    "request_id_value",
    "run_command",
    "timeout_value",
]

CLIENT_PANEL = "Backend connection"

Output = Annotated[
    OutputFormat,
    typer.Option(
        "--output",
        "-o",
        "--format",
        help="json: one envelope on stdout. ndjson: one object per line. text: for people.",
        case_sensitive=False,
    ),
]
EndpointUrl = Annotated[
    str | None,
    typer.Option(
        "--endpoint",
        help="Backend URL on this computer (http://127.0.0.1:PORT) or unix:/path/to.sock. "
        "Defaults to RATICODE_URL, RATICODE_SOCKET, then <data>/headless/endpoint.json.",
        rich_help_panel=CLIENT_PANEL,
    ),
]
TokenFile = Annotated[
    Path | None,
    typer.Option(
        "--token-file",
        help="File holding the API token (mode 600). Defaults to RATICODE_TOKEN, "
        "RATICODE_TOKEN_FILE, then <data>/headless/owner-token (not in Rem worker sessions).",
        rich_help_panel=CLIENT_PANEL,
    ),
]
DataDir = Annotated[
    Path | None,
    typer.Option(
        "--data-dir",
        help="Raticode data folder to discover the backend from. Defaults to "
        "RATICODE_DATA_DIR, GOFER_DATA_DIR, then the standard folder.",
        rich_help_panel=CLIENT_PANEL,
    ),
]
RequestId = Annotated[
    str | None,
    typer.Option(
        "--request-id",
        help="Idempotency key sent as request_id. Only Rem messages, tasks, run controls "
        "and thread messages, and board ticket create and comment, deduplicate on it: "
        "reuse the ID when retrying those. Other endpoints ignore it. 'auto' generates "
        "one and prints it in the result.",
    ),
]
ExpectedRevision = Annotated[
    str | None,
    typer.Option(
        "--expected-revision",
        help="Fail with exit 4 instead of overwriting if the record changed since this "
        "revision. Sent as expected_revision on /rem/ paths and expectedRevision "
        "elsewhere (inside params for organizations); a number when it is all digits.",
    ),
]
Timeout = Annotated[
    str | None,
    typer.Option("--timeout", help="Longest to wait, such as 30s, 5m or 1h30m."),
]
Interval = Annotated[
    float,
    typer.Option("--interval", min=0.05, help="Seconds between polls."),
]
NonInteractive = Annotated[
    bool,
    typer.Option(
        "--non-interactive",
        help="Never prompt. Also set by RATICODE_NON_INTERACTIVE=1, CI=true or no terminal.",
    ),
]
Yes = Annotated[bool, typer.Option("--yes", "-y", help="Confirm destructive actions.")]


def connect(
    endpoint: str | None = None,
    token_file: Path | None = None,
    data_dir: Path | None = None,
    *,
    timeout: float | None = DEFAULT_REQUEST_TIMEOUT,
    retries: int = 2,
) -> ApiClient:
    """Discover the local backend and return a client for it."""
    return ApiClient(
        discover_endpoint(endpoint=endpoint, token_file=token_file, data_dir=data_dir),
        timeout=timeout,
        retries=retries,
    )


def request_id_value(value: str | None) -> str | None:
    """``--request-id`` as sent: ``auto`` becomes a new UUID; blanks are refused."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        raise CliError(
            "--request-id can't be empty.", code="invalid_request_id", exit_code=ExitCode.USAGE
        )
    if text.lower() == "auto":
        return new_request_id()
    if len(text) > 200 or any(ord(ch) < 32 for ch in text):
        raise CliError(
            "--request-id must be at most 200 printable characters.",
            code="invalid_request_id",
            exit_code=ExitCode.USAGE,
        )
    return text


def expected_revision_value(value: str | None) -> int | str | None:
    """``--expected-revision``: digits become an int (organizations), others stay text."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        raise CliError(
            "--expected-revision can't be empty.",
            code="invalid_revision",
            exit_code=ExitCode.USAGE,
        )
    return int(text) if text.isdigit() else text


def timeout_value(value: str | None) -> float | None:
    return parse_duration(value)
