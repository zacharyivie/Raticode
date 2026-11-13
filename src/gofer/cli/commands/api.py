"""``Raticode api``, ``Raticode wait`` and ``Raticode completion``.

``api`` is the generic escape hatch over the local backend, like ``gh api``: every
endpoint is reachable even before it has a resource command.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from gofer.cli import client_options as opts
from gofer.cli.client import (
    METHODS,
    CliError,
    CommandResult,
    ExitCode,
    Output,
    api_path,
    field_classifier,
    in_agent_session,
    wait_until,
)
from gofer.ui.headless import AGENT_SECOND_BRAIN_ACTIONS, agent_request_refusal

# Organization actions only the user may send through `api` from inside an agent turn.
# They start, pause or resume work, decide consent requests, change what needs consent,
# launch agent work, or touch credentials (docs/cli-parity.json labels them
# org-run-control, consent-gated, runs-agents and credentials; a test keeps the two in
# step). Rem asks for these through its own tools, where the app records a consent request.
USER_ONLY_ORG_ACTIONS = frozenset(
    {
        # org-run-control
        "control",
        "resume_all",
        "heartbeat_resume",
        "request_decide",
        "request_approve_all",
        "auto_mode",
        # consent-gated
        "archive",
        "execution_resolve",
        "import_github",
        "import_github_preview",
        "routine_save",
        "webhook_save",
        "restore",
        # runs-agents
        "approve",
        "review",
        "reopen",
        "check_run",
        "execution_reconcile",
        "routine_trigger",
        "wake",
        "workflow_launch",
        # credentials
        "secret_store",
        "secret_reference",
    }
)

# Path prefixes offered by shell completion for `Raticode api`. They are the routes
# docs/cli-parity.json doesn't exclude, cut at the first `*` segment. The manifest
# isn't installed with the CLI, so the list lives here and
# tests/unit/test_cli_client.py::test_api_path_completion_matches_the_parity_manifest
# fails, printing the new tuple, when the two drift.
API_PATHS = (
    "/chat/events",
    "/chat/providers",
    "/chat/steer",
    "/chat/steering",
    "/chat/stop",
    "/devices",
    "/doctor",
    "/health",
    "/organizations",
    "/provider/auth",
    "/provider/capabilities",
    "/provider/profiles",
    "/provider/settings",
    "/queue",
    "/rattish/triggers",
    "/rattish/triggers/login",
    "/rattish/triggers/preview",
    "/rattish/workflows",
    "/rattish/workflows/import",
    "/rattish/workflows/import/preview",
    "/rem-resources",
    "/rem/context",
    "/rem/conversation",
    "/rem/events",
    "/rem/kanban",
    "/rem/kanban/participant",
    "/rem/kanban/report/seen",
    "/rem/kanban/settings",
    "/rem/kanban/ticket",
    "/rem/kanban/timeline",
    "/rem/kanban/window",
    "/rem/memories",
    "/rem/memory-mode",
    "/rem/messages",
    "/rem/notices",
    "/rem/reset",
    "/rem/runs",
    "/rem/search",
    "/rem/tasks",
    "/rem/threads",
    "/retention",
    "/runners",
    "/second-brains",
    "/swarms",
    "/usage/overview",
    "/usage/refresh",
    "/workflows",
    "/workflows/import",
    "/workflows/import/preview",
)


def complete_method(incomplete: str) -> list[str]:
    return [method for method in METHODS if method.startswith(incomplete.upper())]


def complete_api_path(incomplete: str) -> list[str]:
    typed = incomplete.removeprefix("/api") if incomplete.startswith("/api/") else incomplete
    if typed and not typed.startswith("/"):
        typed = "/" + typed
    return [path for path in API_PATHS if path.startswith(typed)]


def _read_source(value: str, what: str) -> str:
    """``@-`` is stdin, ``@path`` a file, anything else the literal text."""
    if value == "@-":
        return sys.stdin.read()
    if value.startswith("@"):
        path = Path(value[1:]).expanduser()
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CliError(
                f"Could not read {what} from {path}: {exc.strerror or exc}",
                code="invalid_input",
                exit_code=ExitCode.USAGE,
            ) from exc
    return value


def _set_nested(target: dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    if not all(parts):
        raise CliError(
            f"Invalid field name {key!r}.", code="invalid_field", exit_code=ExitCode.USAGE
        )
    current = target
    for part in parts[:-1]:
        existing = current.get(part)
        if existing is None:
            existing = current[part] = {}
        if not isinstance(existing, dict):
            raise CliError(
                f"Field {key!r} conflicts with a non-object value.",
                code="invalid_field",
                exit_code=ExitCode.USAGE,
            )
        current = existing
    current[parts[-1]] = value


def _parse_fields(raw: list[str], typed: list[str]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for item, is_typed in [(value, False) for value in raw] + [(value, True) for value in typed]:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise CliError(
                f"Fields look like key=value, got {item!r}.",
                code="invalid_field",
                exit_code=ExitCode.USAGE,
            )
        parsed: Any = value
        if is_typed:
            if value.startswith("@"):
                parsed = _read_source(value, f"field {key}")
            else:
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    parsed = value
        _set_nested(fields, key, parsed)
    return fields


def _request_body(data: str | None, fields: dict[str, Any]) -> Any:
    body: Any = None
    if data is not None:
        text = _read_source(data, "--data")
        try:
            body = json.loads(text) if text.strip() else None
        except json.JSONDecodeError as exc:
            raise CliError(
                f"--data is not valid JSON: {exc.msg} at line {exc.lineno}.",
                code="invalid_json",
                exit_code=ExitCode.USAGE,
            ) from exc
    if fields:
        if body is None:
            body = {}
        if not isinstance(body, dict):
            raise CliError(
                "Fields can only be combined with a JSON object body.",
                code="invalid_field",
                exit_code=ExitCode.USAGE,
            )
        for key, value in fields.items():
            if isinstance(value, dict) and isinstance(body.get(key), dict):
                body[key] = {**body[key], **value}
            else:
                body[key] = value
    return body


def _body_action(body: Any, query: dict[str, Any]) -> str:
    if isinstance(body, dict) and body.get("action") is not None:
        return str(body["action"])
    return str(query.get("action") or "")


def agent_api_refusal(method: str, path: str, body: Any, query: dict[str, Any]) -> str | None:
    """Why `Raticode api` won't send this request from inside an agent turn, or None.

    An agent turn may only do what a limited agent token may (gofer.ui.headless), and
    never the organization decisions in USER_ONLY_ORG_ACTIONS, even with a token that
    would let it. This is a guard in the client, not a boundary; the backend applies its
    own rules to whichever token arrives.
    """
    full = api_path(path)
    route = full.split("?", 1)[0]
    reason = agent_request_refusal(method, route)
    if reason is not None:
        return reason
    if route == "/api/organizations" and _body_action(body, query) in USER_ONLY_ORG_ACTIONS:
        return (
            "Starting, pausing or resuming an organization, deciding its consent requests, "
            "launching its work and changing its credentials are yours to do in the "
            "Organizations tab."
        )
    if (
        route == "/api/second-brains"
        and method != "GET"
        and (_body_action(body, query) or "snapshot") not in AGENT_SECOND_BRAIN_ACTIONS
    ):
        return "An agent turn can read Second Brain through `api` but can't change it."
    return None


def _is_error_event(event: Any) -> bool:
    return isinstance(event, dict) and event.get("type") == "error"


def api_command(
    method: Annotated[
        str,
        typer.Argument(help="GET, POST, PUT, PATCH or DELETE.", autocompletion=complete_method),
    ],
    path: Annotated[
        str,
        typer.Argument(
            help="API path such as /rem/kanban; the /api prefix is optional.",
            autocompletion=complete_api_path,
        ),
    ],
    data: Annotated[
        str | None,
        typer.Option("--data", "-d", help="JSON body, @file, or @- to read stdin."),
    ] = None,
    raw_field: Annotated[
        list[str] | None,
        typer.Option(
            "--raw-field",
            "-f",
            help="key=value string. Query parameter for GET and DELETE, else a body field. "
            "Dots nest: params.taskId=t1.",
        ),
    ] = None,
    typed_field: Annotated[
        list[str] | None,
        typer.Option(
            "--field",
            "-F",
            help="key=value parsed as JSON when it is valid JSON (true, 3, null, [..]); "
            "@file reads the value from a file.",
        ),
    ] = None,
    request_id: opts.RequestId = None,
    expected_revision: opts.ExpectedRevision = None,
    follow: Annotated[
        bool,
        typer.Option(
            "--follow",
            help="Stream an NDJSON endpoint, or poll a GET that returns an events list, "
            "writing one JSON object per line.",
        ),
    ] = False,
    interval: opts.Interval = 2.0,
    timeout: opts.Timeout = None,
    raw: Annotated[
        bool,
        typer.Option("--raw", help="Print the response body as-is instead of the envelope."),
    ] = False,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
    non_interactive: opts.NonInteractive = False,
) -> None:
    """Call any endpoint of the local Raticode backend, like `gh api`.

    Prints {"ok", "command", "data", "error", "warnings"} on stdout. Exit codes: 0 ok,
    1 failed, 2 invalid input, 3 not found, 4 conflict or stale revision, 5 not
    authorized, 6 backend unreachable, 7 needs the user, 8 timeout or outcome unknown.
    """
    del non_interactive  # `api` never prompts; accepted so scripts can pass it everywhere.
    rid_holder: dict[str, str | None] = {"rid": None}

    def body(out: Output) -> CommandResult | None:
        verb = method.upper()
        if verb not in METHODS:
            raise CliError(
                f"Unsupported method {method!r}. Use one of {', '.join(METHODS)}.",
                code="invalid_method",
                exit_code=ExitCode.USAGE,
            )
        rid = opts.request_id_value(request_id)
        rid_holder["rid"] = rid
        revision = opts.expected_revision_value(expected_revision)
        seconds = opts.timeout_value(timeout)
        fields = _parse_fields(raw_field or [], typed_field or [])
        query: dict[str, Any] = {}
        payload: Any
        if verb in {"GET", "DELETE"} and data is None:
            for key, value in fields.items():
                query[key] = value if isinstance(value, str) else json.dumps(value)
            payload = None
        else:
            payload = _request_body(data, fields)
        if revision is not None and payload is None and verb not in {"GET", "DELETE"}:
            payload = {}
        if in_agent_session():
            refusal = agent_api_refusal(verb, path, payload, query)
            if refusal is not None:
                raise CliError(
                    f"{refusal} This command is running inside a Raticode agent turn; "
                    "run it from your own terminal or use the app.",
                    code="user_only",
                    exit_code=ExitCode.AUTH,
                )
        client = opts.connect(endpoint, token_file, data_dir, timeout=seconds or 30.0)
        if follow:
            out.begin_stream()
            count = 0
            last_error: Any = None
            try:
                for event in client.follow(
                    verb,
                    path,
                    body=payload,
                    query=query,
                    request_id=rid,
                    expected_revision=revision,
                    interval=interval,
                    timeout=seconds,
                ):
                    count += 1
                    if raw:
                        sys.stdout.write(json.dumps(event, separators=(",", ":")) + "\n")
                        sys.stdout.flush()
                    else:
                        out.event(event)
                    last_error = event if _is_error_event(event) else None
            except KeyboardInterrupt:
                return (
                    None
                    if raw
                    else CommandResult(data={"events": count, "interrupted": True}, request_id=rid)
                )
            if last_error is not None:
                raise CliError(
                    str(last_error.get("error") or last_error.get("message") or "Stream failed"),
                    code="stream_error",
                    exit_code=ExitCode.FAILED,
                    data={"events": count},
                )
            return None if raw else CommandResult(data={"events": count}, request_id=rid)
        response = client.request(
            verb,
            path,
            body=payload,
            query=query,
            request_id=rid,
            expected_revision=revision,
        )
        if raw:
            if isinstance(response.data, str):
                sys.stdout.write(response.data)
            elif response.data is not None:
                sys.stdout.write(json.dumps(response.data) + "\n")
            sys.stdout.flush()
            return None
        return CommandResult(data=response.data, request_id=rid)

    def run(out: Output) -> CommandResult | None:
        try:
            return body(out)
        except CliError as exc:
            # Failures carry the request ID too, so a retry can reuse it.
            exc.request_id = exc.request_id or rid_holder["rid"]
            raise

    opts.run_command("api", output, run)


def _states(values: list[str] | None) -> list[str]:
    states: list[str] = []
    for value in values or []:
        states.extend(part.strip() for part in value.split(",") if part.strip())
    return states


def wait_command(
    path: Annotated[
        str,
        typer.Argument(
            help="GET path to poll, such as /rem/runs/RUN_ID.",
            autocompletion=complete_api_path,
        ),
    ],
    field: Annotated[
        str,
        typer.Option(
            "--field",
            help="Dotted path of the state in the response, such as run.state.",
        ),
    ] = "status",
    success: Annotated[
        list[str] | None,
        typer.Option("--success", help="State(s) that mean done (comma-separated or repeated)."),
    ] = None,
    failure: Annotated[
        list[str] | None,
        typer.Option("--failure", help="State(s) that mean failed: exit 1."),
    ] = None,
    needs_user: Annotated[
        list[str] | None,
        typer.Option("--needs-user", help="State(s) that wait on you: exit 7."),
    ] = None,
    query_field: Annotated[
        list[str] | None,
        typer.Option("--raw-field", "-f", help="key=value query parameter."),
    ] = None,
    timeout: opts.Timeout = "30m",
    interval: opts.Interval = 2.0,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Poll a GET endpoint until a field reaches a terminal state.

    Exits 0 on a --success state, 1 on --failure, 7 on --needs-user, and 8 when
    --timeout passes first. A backend restart while waiting is tolerated.
    """

    def body(out: Output) -> CommandResult:
        ok_states, bad_states, user_states = (
            _states(success),
            _states(failure),
            _states(needs_user),
        )
        if not ok_states:
            raise CliError(
                "Give at least one --success state.",
                code="invalid_input",
                exit_code=ExitCode.USAGE,
            )
        seconds = opts.timeout_value(timeout)
        query = {
            key: value if isinstance(value, str) else json.dumps(value)
            for key, value in _parse_fields(query_field or [], []).items()
        }
        client = opts.connect(endpoint, token_file, data_dir)
        result = wait_until(
            lambda: client.get(path, query=query),
            field_classifier(field, success=ok_states, failure=bad_states, needs_user=user_states),
            timeout=seconds,
            interval=interval,
        )
        if result.exit_code == ExitCode.FAILED:
            raise CliError(
                "Reached a failure state.",
                code="failed",
                exit_code=ExitCode.FAILED,
                data=result.value,
            )
        if result.exit_code == ExitCode.NEEDS_USER:
            raise CliError(
                "Waiting on you.",
                code="needs_user",
                exit_code=ExitCode.NEEDS_USER,
                data=result.value,
            )
        return CommandResult(data=result.value)

    opts.run_command("wait", output, body)


SHELLS = ("bash", "zsh", "fish", "powershell")


def completion_command(
    shell: Annotated[
        str,
        typer.Argument(
            help="bash, zsh, fish or powershell.",
            autocompletion=lambda incomplete: [s for s in SHELLS if s.startswith(incomplete)],
        ),
    ],
) -> None:
    """Print the shell completion script for Raticode.

    Example: `Raticode completion bash > ~/.local/share/bash-completion/completions/Raticode`.
    """
    name = shell.lower()
    if name not in SHELLS:
        typer.echo(f"Unsupported shell {shell!r}. Use one of: {', '.join(SHELLS)}.", err=True)
        raise typer.Exit(int(ExitCode.USAGE))
    try:
        # Typer has no public API for printing a script without installing it.
        from typer._completion_shared import get_completion_script
    except ImportError:
        typer.echo(
            "This build's Typer can't print completion scripts. "
            "Use `Raticode --install-completion` instead.",
            err=True,
        )
        raise typer.Exit(int(ExitCode.FAILED)) from None
    typer.echo(
        get_completion_script(prog_name="Raticode", complete_var="_RATICODE_COMPLETE", shell=name)
    )
