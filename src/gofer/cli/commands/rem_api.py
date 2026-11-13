"""Rem chat, tasks, memories, alerts and settings, through the running backend.

Each command is one or two API calls. Nothing here opens Rem's database: the backend is
its only writer, and its rules apply as they do in the app.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer

from gofer.cli import client_options as opts
from gofer.cli.client import ApiClient, CliError, CommandResult, ExitCode, Output, wait_until
from gofer.cli.commands import _backend as backend

# Rem run states that are still going (``rem_home_models.ACTIVE``).
ACTIVE_RUNS = {"queued", "executing", "verifying", "repairing", "cancelling"}

Connection = tuple[str | None, Path | None, Path | None]

_FILE = typer.Option(None, "--file", help="Read the text from this file")

settings_app = typer.Typer(
    help="Read and apply Rem's settings as a JSON file.", no_args_is_help=True
)
memories_app = typer.Typer(help="List and change what Rem remembers.", no_args_is_help=True)
tasks_app = typer.Typer(help="Rem's tasks: list, create, edit, run and wait.", no_args_is_help=True)
runs_app = typer.Typer(
    help="One run of a Rem task: show, pause, resume, cancel, acknowledge.", no_args_is_help=True
)
thread_app = typer.Typer(help="A Rem task's work thread.", no_args_is_help=True)


def register(app: typer.Typer) -> None:
    """Add the API commands to the ``rem`` group next to its skill and MCP commands."""
    app.info.help = (
        "Rem from the command line. Chat, tasks, memories, alerts and settings go through "
        "the running backend; skill and mcp manage the library Rem threads can use."
    )
    app.command("say")(say)
    app.command("messages")(messages)
    app.command("search")(search)
    app.command("follow")(follow)
    app.command("notices")(notices)
    app.add_typer(settings_app, name="settings")
    app.add_typer(memories_app, name="memories")
    app.add_typer(tasks_app, name="tasks")
    app.add_typer(runs_app, name="runs")
    app.add_typer(thread_app, name="thread")


# Chat -----------------------------------------------------------------------------


def _message_line(message: dict[str, Any]) -> str:
    who = "you" if message.get("role") == "user" else str(message.get("role") or "rem")
    body = " ".join(str(message.get("body") or "").split())
    return f"[{message.get('seq', '')}] {who}: {body}"


def _answered(page: dict[str, Any], identity: str, seen: bool) -> bool:
    state = page["conversation"]
    if identity in state.get("waiting", []) or not seen:
        return False
    return bool(state.get("processed") == identity or state.get("pending") != identity)


def _position(client: ApiClient) -> int:
    return int(client.get("/rem/messages", query={"tail": 0})["last"])


def say(
    text: str = typer.Argument(None, help="The message, or - to read it from stdin"),
    file: Path | None = _FILE,
    refers_to: str | None = typer.Option(
        None, "--refers-to", help="ID of an earlier chat message this one replies to"
    ),
    wait: bool = typer.Option(False, "--wait", help="Wait for Rem's reply and print it"),
    timeout: opts.Timeout = None,
    request: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Send Rem a chat message, as if typed in the app."""
    identity = backend.request_id(request)

    def body(client: ApiClient, out: Output) -> CommandResult:
        message = backend.read_body(text, file, "message").strip()
        if not message:
            raise backend.invalid("Write a message.")
        limit = opts.timeout_value(timeout)
        cursor = _position(client) if wait else 0
        payload: dict[str, Any] = {"body": message}
        if refers_to:
            payload["refers_to"] = refers_to
        accepted = client.post("/rem/messages", payload, request_id=identity)
        if not wait:
            return CommandResult(data=accepted, text=f"Sent {identity}.")
        replies: list[dict[str, Any]] = []
        seen = False

        def fetch() -> dict[str, Any]:
            nonlocal cursor, seen
            page: dict[str, Any] = client.get(
                "/rem/messages", query={"after": cursor, "limit": 300}
            )
            for item in page["messages"]:
                if item.get("id") == identity:
                    seen = True
                elif seen and item.get("role") != "user":
                    replies.append(item)
            cursor = page["last"]
            return page

        def done(page: dict[str, Any]) -> ExitCode | None:
            if page["last"] < page["latest"] or not _answered(page, identity, seen):
                return None
            return ExitCode.OK

        wait_until(fetch, done, timeout=limit, interval=1.0, max_interval=3.0)
        lines = "\n".join(_message_line(m) for m in replies) or "Rem sent no reply."
        return CommandResult(data={**accepted, "replies": replies}, text=lines)

    backend.run("rem say", output, (endpoint, token_file, data_dir), body, request_id=identity)


def messages(
    tail: int = typer.Option(20, "--tail", "-n", min=0, max=300, help="The newest N messages"),
    after: int | None = typer.Option(
        None, "--after", min=0, help="Messages after this position (seq) instead"
    ),
    limit: int = typer.Option(
        100, "--limit", min=1, max=300, help="At most this many, with --after"
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Read Rem's chat. Pass the answer's last position as --after to read only new messages."""

    def body(client: ApiClient, out: Output) -> CommandResult:
        query = {"tail": tail} if after is None else {"after": after, "limit": limit}
        page = client.get("/rem/messages", query=query)
        text = "\n".join(_message_line(m) for m in page["messages"]) or "No messages."
        return CommandResult(data=page, text=text)

    backend.run("rem messages", output, (endpoint, token_file, data_dir), body)


def search(
    query: str = typer.Argument(..., help="Words to find in the whole saved chat"),
    before: int | None = typer.Option(None, "--before", help="Continue from this position"),
    limit: int = typer.Option(50, "--limit", min=1, max=50),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Search Rem's chat for words. No model reads the query."""

    def body(client: ApiClient, out: Output) -> CommandResult:
        found = client.get("/rem/search", query={"q": query, "before": before, "limit": limit})
        return CommandResult(data=found)

    backend.run("rem search", output, (endpoint, token_file, data_dir), body)


def _stream(
    out: Output,
    read: Callable[[], tuple[list[Any], bool]],
    limit: float | None,
    interval: float,
) -> int:
    """Print what ``read`` returns until nothing more is stored, then wait; returns a count.

    Ends at ``limit`` with a ``timeout`` error (exit 8), or quietly on Ctrl+C.
    """
    out.begin_stream()
    deadline = None if limit is None else time.monotonic() + limit
    printed = 0
    try:
        while True:
            more = True
            while more:
                items, more = read()
                for item in items:
                    out.event(item)
                    printed += 1
            if deadline is not None and time.monotonic() >= deadline:
                raise CliError(
                    "Stopped following at the timeout.",
                    code="timeout",
                    exit_code=ExitCode.UNKNOWN,
                    data={"events": printed},
                )
            pause = interval if deadline is None else min(interval, deadline - time.monotonic())
            time.sleep(max(0.0, pause))
    except KeyboardInterrupt:
        return printed


def follow(
    after: int | None = typer.Option(
        None, "--after", min=0, help="Start after this position; default is now"
    ),
    timeout: opts.Timeout = None,
    interval: opts.Interval = 2.0,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Print new chat messages as they arrive, one JSON line each, until stopped.

    Each message is a line {"kind": "event", "data": ...}. With --timeout the command
    ends with exit 8 when the time runs out.
    """

    def body(client: ApiClient, out: Output) -> CommandResult:
        cursor = _position(client) if after is None else after

        def read() -> tuple[list[Any], bool]:
            nonlocal cursor
            page = client.get("/rem/messages", query={"after": cursor, "limit": 300})
            cursor = page["last"]
            return page["messages"], page["last"] < page["latest"]

        printed = _stream(out, read, opts.timeout_value(timeout), interval)
        return CommandResult(data={"events": printed, "last": cursor})

    backend.run("rem follow", output, (endpoint, token_file, data_dir), body)


def _notice_line(notice: dict[str, Any]) -> str:
    return f"{notice['reason']}: {notice.get('title') or notice.get('body', '')}"


def notices(
    cursor: str = typer.Option(
        "", "--cursor", help="The cursor from the last read; without it, only the cursor"
    ),
    include: str = typer.Option(
        "", "--include", help="Comma list of needs_you, finished, proposals (default: all)"
    ),
    watch: bool = typer.Option(False, "--watch", help="Keep reading and print each alert"),
    timeout: opts.Timeout = None,
    interval: opts.Interval = 10.0,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Alerts for anything that needs you: stuck or finished tasks, proposals, organizations.

    The first read without --cursor returns no alerts, only the cursor to start from, so
    a new script isn't flooded with history. Use `rem tasks list --needs-you` for what is
    waiting right now.
    """

    def body(client: ApiClient, out: Output) -> CommandResult:
        if not watch:
            found = client.get("/rem/notices", query={"cursor": cursor, "include": include})
            lines = [*map(_notice_line, found["notices"]), f"cursor {found['cursor']}"]
            return CommandResult(data=found, text="\n".join(lines))
        position = cursor

        def read() -> tuple[list[Any], bool]:
            nonlocal position
            found = client.get("/rem/notices", query={"cursor": position, "include": include})
            position = found["cursor"]
            return [{**n, "cursor": position} for n in found["notices"]], bool(found["more"])

        printed = _stream(out, read, opts.timeout_value(timeout), interval)
        return CommandResult(data={"events": printed, "cursor": position})

    backend.run("rem notices", output, (endpoint, token_file, data_dir), body)


# Settings -------------------------------------------------------------------------

# Settings the window keeps for itself: they describe the desktop, not Rem.
_WINDOW_ONLY = ("workspace_snapshot",)


@settings_app.command("show")
def settings_show(
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show Rem's settings: providers, models, projects, resources and board."""
    backend.run(
        "rem settings show",
        output,
        (endpoint, token_file, data_dir),
        lambda client, out: CommandResult(data=client.get("/rem/context")),
    )


@settings_app.command("export")
def settings_export(
    file: Path | None = typer.Option(None, "--file", "-f", help="Write here instead of stdout"),
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Write Rem's settings as a JSON file that `rem settings apply` accepts.

    Without --file the settings themselves are printed, not an envelope.
    """

    def body(client: ApiClient, out: Output) -> CommandResult | None:
        config = client.get("/rem/context")["config"]
        for key in _WINDOW_ONLY:
            config.pop(key, None)
        text = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
        if file is None:
            out.stdout.write(text)
            out.stdout.flush()
            return None
        try:
            file.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise backend.invalid(f"Couldn't write {file}: {exc.strerror}") from None
        return CommandResult(data={"file": str(file)}, text=f"Wrote Rem's settings to {file}.")

    backend.run(
        "rem settings export", opts.OutputFormat.json, (endpoint, token_file, data_dir), body
    )


@settings_app.command("apply")
def settings_apply(
    file: Path = typer.Option(..., "--file", "-f", help="JSON settings file, or - for stdin"),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Replace Rem's settings with a file from `rem settings export`.

    While the desktop window is open it sends its own Rem settings every minute and
    replaces these; change them in Settings > Rem there instead.
    """

    def body(client: ApiClient, out: Output) -> CommandResult:
        backend.require_user("change Rem's settings")
        config = backend.read_json_file(file)
        if not isinstance(config, dict):
            raise backend.invalid("Rem's settings file must hold a JSON object.")
        applied = client.post("/rem/context", config)
        return CommandResult(
            data=applied,
            text="Applied Rem's settings.",
            warnings=[str(w) for w in applied.get("warnings") or []],
        )

    backend.run("rem settings apply", output, (endpoint, token_file, data_dir), body)


# Memories -------------------------------------------------------------------------


def _memories(data: dict[str, Any]) -> CommandResult:
    lines = [f"{m['id']}  {m['text']}" for m in data.get("memories", [])]
    return CommandResult(data=data, text="\n".join([f"Memory is {data.get('mode')}.", *lines]))


@memories_app.command("list")
def memories_list(
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List what Rem remembers, newest first, and whether memory is on."""
    backend.run(
        "rem memories list",
        output,
        (endpoint, token_file, data_dir),
        lambda client, out: _memories(client.get("/rem/memories")),
    )


@memories_app.command("add")
def memories_add(
    text: str = typer.Argument(...),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Remember a fact. Text that looks like a password or key is refused."""
    backend.run(
        "rem memories add",
        output,
        (endpoint, token_file, data_dir),
        lambda client, out: _memories(client.post("/rem/memories", {"text": text})),
    )


@memories_app.command("edit")
def memories_edit(
    memory_id: str = typer.Argument(...),
    text: str = typer.Argument(...),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Correct one remembered fact."""
    backend.run(
        "rem memories edit",
        output,
        (endpoint, token_file, data_dir),
        lambda client, out: _memories(
            client.patch(f"/rem/memories/{backend.segment(memory_id)}", {"text": text})
        ),
    )


@memories_app.command("forget")
def memories_forget(
    memory_id: str = typer.Argument(...),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Forget one remembered fact, including in the notes on replies that saved it."""
    backend.run(
        "rem memories forget",
        output,
        (endpoint, token_file, data_dir),
        lambda client, out: _memories(client.delete(f"/rem/memories/{backend.segment(memory_id)}")),
    )


@memories_app.command("mode")
def memories_mode(
    mode: str = typer.Argument(..., help="on, paused or off"),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Turn memory on, pause new saves, or turn it off."""
    backend.run(
        "rem memories mode",
        output,
        (endpoint, token_file, data_dir),
        lambda client, out: _memories(client.put("/rem/memory-mode", {"mode": mode})),
    )


@memories_app.command("undo")
def memories_undo(
    message_id: str = typer.Argument(..., help="The Rem reply whose memory changes to undo"),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Reverse the memory changes one Rem reply made."""
    backend.run(
        "rem memories undo",
        output,
        (endpoint, token_file, data_dir),
        lambda client, out: _memories(
            client.post(f"/rem/messages/{backend.segment(message_id)}/undo-memory", {})
        ),
    )


# Tasks ----------------------------------------------------------------------------


def _needs_user(task: dict[str, Any]) -> bool:
    if (task.get("dependency") or {}).get("state") == "held":
        return True
    run = task.get("active_run") or task.get("last_run") or {}
    return run.get("needs_user") is True


def _sleep(run: dict[str, Any]) -> dict[str, Any] | None:
    """A run's worker sleep while it is parked (rem_home_sleep), else None."""
    record = run.get("sleep")
    if run.get("state") != "queued" or not isinstance(record, dict) or not record.get("asleep"):
        return None
    return record


def _task_line(task: dict[str, Any]) -> str:
    run = task.get("active_run") or task.get("last_run") or {}
    waiting = task.get("dependency") or {}
    state = run.get("state") or ("on_hold" if waiting.get("state") == "held" else "")
    state = state or ("waiting" if waiting else task.get("state") or "")
    flag = " (needs you)" if _needs_user(task) else ""
    if waiting:
        flag += f" - {waiting.get('reason', '')}"
    asleep = _sleep(run)
    if asleep:
        state = "sleeping"
        until = str(asleep.get("until") or "")[11:16]
        flag += (
            f" - waiting for {asleep.get('waiting_for') or 'an outside event'}"
            + (f", next check {until} UTC" if until else "")
            + f", woke {int(asleep.get('wakes') or 0)} times"
        )
    return f"{task['id']}  {state:<11} {task.get('type', ''):<9} {task.get('title', '')}{flag}"


def _task(client: ApiClient, identity: str) -> dict[str, Any]:
    for task in client.get("/rem/tasks")["tasks"]:
        if task["id"] == identity:
            return dict(task)
    raise CliError(f"No Rem task {identity}.", code="not_found", exit_code=ExitCode.NOT_FOUND)


def _task_result(data: dict[str, Any]) -> CommandResult:
    return CommandResult(data=data, text=_task_line(data["task"]))


@tasks_app.command("list")
def tasks_list(
    needs_you: bool = typer.Option(False, "--needs-you", help="Only tasks waiting on you"),
    active: bool = typer.Option(False, "--active", help="Only tasks with a run going"),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List Rem's tasks with their latest run."""

    def body(client: ApiClient, out: Output) -> CommandResult:
        found = list(client.get("/rem/tasks")["tasks"])
        if needs_you:
            found = [t for t in found if _needs_user(t)]
        if active:
            found = [t for t in found if t.get("active_run")]
        return CommandResult(data=found, text="\n".join(map(_task_line, found)) or "No tasks.")

    backend.run("rem tasks list", output, (endpoint, token_file, data_dir), body)


@tasks_app.command("show")
def tasks_show(
    task_id: str = typer.Argument(...),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show one task, its revision and recent runs."""

    def body(client: ApiClient, out: Output) -> CommandResult:
        task = _task(client, task_id)
        return CommandResult(data=task, text=_task_line(task))

    backend.run("rem tasks show", output, (endpoint, token_file, data_dir), body)


def _recipe(
    title: str | None,
    kind: str | None,
    prompt: str | None,
    prompt_file: Path | None,
    project: Path | None,
    schedule: str | None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if title is not None:
        fields["title"] = title
    if kind is not None:
        fields["type"] = kind
    if prompt is not None or prompt_file is not None:
        fields["prompt"] = backend.read_body(prompt, prompt_file, "prompt")
    if project is not None:
        fields["project_dir"] = str(project.expanduser().resolve())
    if schedule is not None:
        try:
            fields["schedule"] = json.loads(schedule)
        except ValueError:
            raise backend.invalid('--schedule takes JSON, such as {"kind": "once"}.') from None
    return fields


_TITLE = typer.Option(None, "--title")
_TYPE = typer.Option(None, "--type", help="research, maintain, develop, errand or reminder")
_PROMPT = typer.Option(None, "--prompt", help="What Rem should do, or - for stdin")
_PROMPT_FILE = typer.Option(None, "--prompt-file")
_PROJECT = typer.Option(None, "--project", help="Project folder the work happens in")
_SCHEDULE = typer.Option(
    None,
    "--schedule",
    help='JSON schedule, such as {"kind": "daily", "local_time": "09:00", "timezone": "UTC"}',
)
_REVISION = typer.Option(..., "--expected-revision", help="The task's revision from `show`")
_AFTER = typer.Option(
    None,
    "--after",
    help="Start only after this task or run succeeds. Repeat for several; one-time tasks only",
)


@tasks_app.command("create")
def tasks_create(
    title: str = typer.Option(..., "--title"),
    kind: str = typer.Option(..., "--type", help="research, maintain, develop, errand or reminder"),
    prompt: str | None = _PROMPT,
    prompt_file: Path | None = _PROMPT_FILE,
    project: Path | None = _PROJECT,
    schedule: str | None = _SCHEDULE,
    after: list[str] | None = _AFTER,
    spec: Path | None = typer.Option(
        None, "--spec", help="JSON task fields (provider, model, acceptance_criteria...)"
    ),
    request: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Create a Rem task. With no schedule it runs once, now; with --after, once those succeed."""
    identity = backend.request_id(request)

    def body(client: ApiClient, out: Output) -> CommandResult:
        extra = backend.read_json_file(spec) if spec else {}
        if not isinstance(extra, dict):
            raise backend.invalid("--spec must hold a JSON object.")
        fields = {**extra, **_recipe(title, kind, prompt, prompt_file, project, schedule)}
        if after:
            fields["after"] = list(after)
        return _task_result(client.post("/rem/tasks", fields, request_id=identity))

    backend.run(
        "rem tasks create", output, (endpoint, token_file, data_dir), body, request_id=identity
    )


@tasks_app.command("edit")
def tasks_edit(
    task_id: str = typer.Argument(...),
    expected_revision: int = _REVISION,
    title: str | None = _TITLE,
    kind: str | None = _TYPE,
    prompt: str | None = _PROMPT,
    prompt_file: Path | None = _PROMPT_FILE,
    project: Path | None = _PROJECT,
    schedule: str | None = _SCHEDULE,
    after: list[str] | None = _AFTER,
    clear_after: bool = typer.Option(
        False, "--clear-after", help="Stop waiting for other tasks and start as scheduled"
    ),
    future_only: bool = typer.Option(
        False, "--future-only", help="Leave a run already going as it is"
    ),
    request: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Change a task. Refused with exit 4 if it changed since --expected-revision."""
    identity = backend.request_id(request)

    def body(client: ApiClient, out: Output) -> CommandResult:
        fields = _recipe(title, kind, prompt, prompt_file, project, schedule)
        if after and clear_after:
            raise backend.invalid("Use --after or --clear-after, not both.")
        if after or clear_after:
            fields["after"] = list(after or [])
        if not fields:
            raise backend.invalid("Change at least one field.")
        if future_only:
            fields["apply_to"] = "future"
        edited = client.patch(
            f"/rem/tasks/{backend.segment(task_id)}",
            fields,
            request_id=identity,
            expected_revision=expected_revision,
        )
        return _task_result(edited)

    backend.run(
        "rem tasks edit", output, (endpoint, token_file, data_dir), body, request_id=identity
    )


def _run_exit(run: dict[str, Any]) -> ExitCode:
    """0 succeeded, 7 waiting on you, 8 outcome unknown, 1 anything else."""
    if run.get("state") == "succeeded":
        return ExitCode.OK
    if run.get("needs_user") is True:
        return ExitCode.NEEDS_USER
    if run.get("state") == "outcome_unknown":
        return ExitCode.UNKNOWN
    return ExitCode.FAILED


def _await_run(
    client: ApiClient, run_id: str, timeout: float | None, interval: float
) -> CommandResult:
    """Wait for a run to end; a run that didn't succeed fails the command with its code."""
    path = f"/rem/runs/{backend.segment(run_id)}"

    def fetch() -> dict[str, Any]:
        return dict(client.get(path)["run"])

    def classify(run: dict[str, Any]) -> ExitCode | None:
        return None if run.get("state") in ACTIVE_RUNS else _run_exit(run)

    result = wait_until(fetch, classify, timeout=timeout, interval=interval)
    run = result.value
    if result.exit_code != ExitCode.OK:
        state = run.get("state")
        waiting = result.exit_code == ExitCode.NEEDS_USER
        raise CliError(
            f"Run {run['id']} ended {state}" + (" and needs you." if waiting else "."),
            code="needs_user" if waiting else f"run_{state}",
            exit_code=result.exit_code,
            data=run,
        )
    return CommandResult(data=run, text=f"Run {run['id']} succeeded.")


@tasks_app.command("run")
def tasks_run(
    task_id: str = typer.Argument(...),
    wait: bool = typer.Option(False, "--wait", help="Wait for the run to finish"),
    timeout: opts.Timeout = None,
    request: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Start a task now. With --wait, exits 0 succeeded, 7 needs you, 1 failed, 8 timeout."""
    identity = backend.request_id(request)

    def body(client: ApiClient, out: Output) -> CommandResult:
        limit = opts.timeout_value(timeout)
        started = client.post(f"/rem/tasks/{backend.segment(task_id)}/run", {}, request_id=identity)
        if not wait:
            return CommandResult(data=started, text=f"Started run {started['run']['id']}.")
        return _await_run(client, started["run"]["id"], limit, 2.0)

    backend.run(
        "rem tasks run", output, (endpoint, token_file, data_dir), body, request_id=identity
    )


@tasks_app.command("wait")
def tasks_wait(
    task_id: str = typer.Argument(...),
    timeout: opts.Timeout = None,
    interval: opts.Interval = 5.0,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Wait for a task's current run. Exits 0 succeeded, 7 needs you, 1 failed, 8 timeout."""

    def body(client: ApiClient, out: Output) -> CommandResult:
        limit = opts.timeout_value(timeout)
        task = _task(client, task_id)
        run = task.get("active_run") or task.get("last_run")
        if not run:
            raise CliError(
                "This task hasn't run yet.", code="not_found", exit_code=ExitCode.NOT_FOUND
            )
        return _await_run(client, run["id"], limit, interval)

    backend.run("rem tasks wait", output, (endpoint, token_file, data_dir), body)


@tasks_app.command("wake")
def tasks_wake(
    task_id: str = typer.Argument(...),
    request: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Wake a task's sleeping run now, without waiting out its sleep."""
    identity = backend.request_id(request)

    def body(client: ApiClient, out: Output) -> CommandResult:
        run = _task(client, task_id).get("active_run") or {}
        if not _sleep(run):
            raise CliError(
                f"Rem task {task_id} isn't sleeping.", code="conflict", exit_code=ExitCode.CONFLICT
            )
        changed = client.post(
            f"/rem/runs/{backend.segment(run['id'])}/wake", {}, request_id=identity
        )
        return CommandResult(data=changed, text=f"Woke task {task_id}; it resumes in its thread.")

    backend.run(
        "rem tasks wake", output, (endpoint, token_file, data_dir), body, request_id=identity
    )


@tasks_app.command("pause")
def tasks_pause(
    task_id: str = typer.Argument(...),
    cancel_current: bool = typer.Option(False, "--cancel-current", help="Also stop a run going"),
    request: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Pause a task's schedule. To pause a run that is going, use `rem runs pause`."""
    identity = backend.request_id(request)

    def body(client: ApiClient, out: Output) -> CommandResult:
        paused = client.post(
            f"/rem/tasks/{backend.segment(task_id)}/pause",
            {"cancel_current": cancel_current},
            request_id=identity,
        )
        return _task_result(paused)

    backend.run(
        "rem tasks pause", output, (endpoint, token_file, data_dir), body, request_id=identity
    )


@tasks_app.command("resume")
def tasks_resume(
    task_id: str = typer.Argument(...),
    expected_revision: int = _REVISION,
    request: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Turn a paused schedule back on. To resume a paused run, use `rem runs resume`."""
    identity = backend.request_id(request)

    def body(client: ApiClient, out: Output) -> CommandResult:
        resumed = client.patch(
            f"/rem/tasks/{backend.segment(task_id)}",
            {"enabled": True},
            request_id=identity,
            expected_revision=expected_revision,
        )
        return _task_result(resumed)

    backend.run(
        "rem tasks resume", output, (endpoint, token_file, data_dir), body, request_id=identity
    )


@tasks_app.command("delete")
def tasks_delete(
    task_id: str = typer.Argument(...),
    yes: opts.Yes = False,
    request: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Delete a task and its schedule, stopping a run that is going. Needs --yes."""
    identity = backend.request_id(request)

    def body(client: ApiClient, out: Output) -> CommandResult:
        if not yes:
            raise backend.invalid("Add --yes to delete this task.")
        deleted = client.delete(f"/rem/tasks/{backend.segment(task_id)}", request_id=identity)
        return CommandResult(data=deleted, text="Deleted.")

    backend.run(
        "rem tasks delete", output, (endpoint, token_file, data_dir), body, request_id=identity
    )


# Runs and threads -----------------------------------------------------------------


@runs_app.command("show")
def runs_show(
    run_id: str = typer.Argument(...),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show one run: state, result, criteria and whether it needs you."""

    def body(client: ApiClient, out: Output) -> CommandResult:
        run = client.get(f"/rem/runs/{backend.segment(run_id)}")["run"]
        return CommandResult(data=run, text=f"Run {run['id']} {run.get('state')}.")

    backend.run("rem runs show", output, (endpoint, token_file, data_dir), body)


def _run_action(
    command: str,
    run_id: str,
    action: str,
    request: str | None,
    output: opts.OutputFormat,
    connection: Connection,
) -> None:
    identity = backend.request_id(request)

    def body(client: ApiClient, out: Output) -> CommandResult:
        changed = client.post(
            f"/rem/runs/{backend.segment(run_id)}/{action}", {}, request_id=identity
        )
        return CommandResult(data=changed, text=f"Run {run_id}: {action} sent.")

    backend.run(command, output, connection, body, request_id=identity)


@runs_app.command("cancel")
def runs_cancel(
    run_id: str = typer.Argument(...),
    request: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Stop a run that is going."""
    connection = (endpoint, token_file, data_dir)
    _run_action("rem runs cancel", run_id, "cancel", request, output, connection)


@runs_app.command("pause")
def runs_pause(
    run_id: str = typer.Argument(...),
    request: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Pause a running run: its turn stops, its thread and worktree stay, its slot frees up."""
    connection = (endpoint, token_file, data_dir)
    _run_action("rem runs pause", run_id, "pause", request, output, connection)


@runs_app.command("resume")
def runs_resume(
    run_id: str = typer.Argument(...),
    request: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Resume a paused run in the same thread, with what it already did."""
    connection = (endpoint, token_file, data_dir)
    _run_action("rem runs resume", run_id, "resume", request, output, connection)


@runs_app.command("acknowledge")
def runs_acknowledge(
    run_id: str = typer.Argument(...),
    request: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Mark a finished or blocked run as seen."""
    connection = (endpoint, token_file, data_dir)
    _run_action("rem runs acknowledge", run_id, "acknowledge", request, output, connection)


@runs_app.command("retry-review")
def runs_retry_review(
    run_id: str = typer.Argument(...),
    request: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Retry a run's interrupted independent review."""
    connection = (endpoint, token_file, data_dir)
    _run_action("rem runs retry-review", run_id, "retry_review", request, output, connection)


@runs_app.command("wake")
def runs_wake(
    run_id: str = typer.Argument(...),
    request: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Wake a sleeping run now; its worker resumes in the same thread."""
    connection = (endpoint, token_file, data_dir)
    _run_action("rem runs wake", run_id, "wake", request, output, connection)


@thread_app.command("show")
def thread_show(
    thread_id: str = typer.Argument(...),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show a Rem task's work thread: messages, results and status."""
    backend.run(
        "rem thread show",
        output,
        (endpoint, token_file, data_dir),
        lambda client, out: CommandResult(
            data=client.get(f"/rem/threads/{backend.segment(thread_id)}")
        ),
    )


@thread_app.command("send")
def thread_send(
    thread_id: str = typer.Argument(...),
    text: str = typer.Argument(None, help="Instructions, or - for stdin"),
    file: Path | None = _FILE,
    request: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Give a task's worker more instructions, as the app's thread input does."""
    identity = backend.request_id(request)

    def body(client: ApiClient, out: Output) -> CommandResult:
        message = backend.read_body(text, file, "instructions").strip()
        if not message:
            raise backend.invalid("Write the instructions.")
        sent = client.post(
            f"/rem/threads/{backend.segment(thread_id)}/messages",
            {"body": message},
            request_id=identity,
        )
        return CommandResult(data=sent, text="Sent.")

    backend.run(
        "rem thread send", output, (endpoint, token_file, data_dir), body, request_id=identity
    )
