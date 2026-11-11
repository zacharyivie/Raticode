"""What the workflow, organization, swarm, Second Brain and system commands share.

They are thin clients of the backend's HTTP API (the one the desktop window and the
phone use), built on :mod:`gofer.cli.client`: the same backend discovery, JSON
envelope, exit codes and options as ``Raticode api``. This module only adds a small
calling convention so each command reads as "make this request, render this text".
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import typer

from gofer.cli import client_options as opts
from gofer.cli.client import ApiClient, CliError, CommandResult, ExitCode, Output


class Backend:
    """An :class:`ApiClient` with the ``request(method, path, body, query=…)`` shape."""

    def __init__(self, client: ApiClient) -> None:
        self.client = client

    def request(
        self,
        method: str,
        path: str,
        body: Any | None = None,
        *,
        query: dict[str, Any] | None = None,
        timeout: float | None = None,
        request_id: str | None = None,
    ) -> Any:
        if method in {"POST", "PUT", "PATCH"} and body is None:
            body = {}
        clean = {key: value for key, value in (query or {}).items() if value is not None}
        return self.client.request(
            method,
            path,
            body=body,
            query=clean or None,
            timeout=timeout,
            request_id=request_id,
        ).data

    def stream(
        self, method: str, path: str, *, query: dict[str, Any] | None = None
    ) -> Iterator[Any]:
        return self.client.stream(method, path, query=query)


@dataclass(frozen=True)
class Session:
    """One invocation: its name, output format and how to reach the backend."""

    command: str
    output: opts.OutputFormat
    endpoint: str | None
    token_file: Path | None
    data_dir: Path | None

    def backend(self) -> Backend:
        return Backend(opts.connect(self.endpoint, self.token_file, self.data_dir))


def session(
    command: str,
    output: opts.OutputFormat,
    endpoint: str | None,
    token_file: Path | None,
    data_dir: Path | None,
) -> Session:
    return Session(command, output, endpoint, token_file, data_dir)


def failure(
    message: str,
    exit_code: ExitCode = ExitCode.FAILED,
    *,
    code: str | None = None,
    data: Any = None,
) -> CliError:
    default = {
        ExitCode.USAGE: "invalid_input",
        ExitCode.NOT_FOUND: "not_found",
        ExitCode.CONFLICT: "conflict",
        ExitCode.AUTH: "forbidden",
        ExitCode.NEEDS_USER: "needs_user",
        ExitCode.UNKNOWN: "timeout",
    }.get(exit_code, "failed")
    return CliError(message, code=code or default, exit_code=exit_code, data=data)


def invalid(current: Session, message: str) -> NoReturn:
    """Refuse bad input before any request, with the usual envelope and exit 2."""
    Output(current.command, current.output).error(failure(message, ExitCode.USAGE))
    raise typer.Exit(int(ExitCode.USAGE))


def finish(
    current: Session,
    call: Callable[[Backend, Output], Any],
    text: Callable[[Any], str | None] | None = None,
    *,
    request_id: str | None = None,
) -> None:
    """Run one command body and print its result; ``call`` raises CliError to fail."""

    def body(out: Output) -> CommandResult:
        data = call(current.backend(), out)
        human = text(data) if text and current.output == opts.OutputFormat.text else None
        return CommandResult(data=data, text=human)

    opts.run_command(current.command, current.output, body, request_id=request_id)


def read_json_file(current: Session, path: Path) -> Any:
    """Read a config file given with -f; ``-`` reads standard input.

    A saved JSON envelope (an export command's output without -f) is unwrapped to its
    ``data``, so ``Raticode org config X > org.json`` round-trips like ``-f org.json``.
    """
    try:
        raw = sys.stdin.read() if str(path) == "-" else path.read_text(encoding="utf-8")
        document = json.loads(raw)
        if isinstance(document, dict) and {"ok", "command", "data"} <= document.keys():
            if document.get("ok") is not True:
                invalid(current, f"{path} holds a failed command's output, not settings")
            return document["data"]
        return document
    except OSError as exc:
        invalid(current, f"Could not read {path}: {exc.strerror or exc}")
    except json.JSONDecodeError as exc:
        invalid(current, f"{path} is not valid JSON: {exc.msg} (line {exc.lineno})")


def write_json_file(path: Path, document: Any) -> None:
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def parse_assignments(current: Session, values: list[str] | None) -> dict[str, Any]:
    """Turn ``key=value`` pairs into a dict; values that parse as JSON keep their type."""
    result: dict[str, Any] = {}
    for item in values or []:
        key, sep, raw = item.partition("=")
        if not sep or not key:
            invalid(current, f"Expected name=value, got {item!r}")
        try:
            result[key] = json.loads(raw)
        except json.JSONDecodeError:
            result[key] = raw
    return result


def duration(current: Session, value: str | None, default: float) -> float:
    try:
        parsed = opts.timeout_value(value)
    except CliError as exc:
        invalid(current, exc.message)
    return default if parsed is None else parsed


def poll(
    check: Callable[[], tuple[bool, Any]],
    *,
    timeout: float,
    interval: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Call ``check`` until it reports done; past the timeout, exit 8 with the last value."""
    deadline = time.monotonic() + timeout
    while True:
        done, value = check()
        if done:
            return value
        if time.monotonic() >= deadline:
            raise failure(f"Still waiting after {timeout:g}s.", ExitCode.UNKNOWN, data=value)
        sleep(min(interval, max(0.0, deadline - time.monotonic())))


def quote(value: str) -> str:
    """Escape one path segment, such as a workflow or run ID."""
    return urllib.parse.quote(value, safe="")


def table(rows: list[list[str]], headers: list[str]) -> str:
    """A plain aligned table; no color or box drawing, so it pipes cleanly."""
    if not rows:
        return ""
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]
    lines = ["  ".join(header.ljust(widths[i]) for i, header in enumerate(headers)).rstrip()]
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return "\n".join(lines)
