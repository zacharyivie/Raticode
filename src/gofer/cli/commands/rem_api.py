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
from urllib.parse import quote

import typer

from gofer.cli import client_options as opts
from gofer.cli.client import ApiClient, CliError, CommandResult, ExitCode, Output, wait_until
from gofer.cli.commands import _backend as backend
from gofer.cli.commands._resources import write_private_text

# Rem run states that are still going (``rem_home_models.ACTIVE``).
ACTIVE_RUNS = {"queued", "executing", "verifying", "repairing", "cancelling"}

Connection = tuple[str | None, Path | None, Path | None]

_FILE = typer.Option(None, "--file", help="Read the text from this file")

settings_app = typer.Typer(
    help="Read and apply Rem's settings as a JSON file.", no_args_is_help=True
)
memories_app = typer.Typer(help="List and change what Rem remembers.", no_args_is_help=True)
decisions_app = typer.Typer(
    help="Rem's decisions: questions waiting for you and the answers Rem reuses.",
    no_args_is_help=True,
)
tasks_app = typer.Typer(help="Rem's tasks: list, create, edit, run and wait.", no_args_is_help=True)
runs_app = typer.Typer(
    help="Runs of Rem tasks: show, pause, resume, cancel, acknowledge one or all.",
    no_args_is_help=True,
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
    app.command("context")(context)
    app.add_typer(settings_app, name="settings")
    app.add_typer(memories_app, name="memories")
    app.add_typer(decisions_app, name="decisions")
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


def _approx(tokens: Any) -> str:
    """An estimated token count, marked with ~ and right-aligned."""
    return f"~{int(tokens or 0):,}".rjust(8)


def _parts_lines(newest: dict[str, Any]) -> list[str]:
    """One call's or turn's parts as aligned lines, then what it sent in all."""
    width = max((len(str(p["label"])) for p in newest["parts"]), default=0)
    lines = [
        f"  {part['label']:<{width}}  {part['chars']:>8,} chars  "
        f"{_approx(part['tokens_estimate'])} tokens"
        f"{'  in a file' if part.get('file') else '' if part['sent'] else '  kept'}"
        for part in newest["parts"]
    ]
    lines.append(
        f"  {'Sent':<{width}}  {newest['sent_chars']:>8,} chars  "
        f"{_approx(newest['sent_tokens_estimate'])} tokens"
    )
    return lines


def _context_text(found: dict[str, Any]) -> str:
    """The newest turn's parts as aligned lines, then the earlier turns' totals."""
    turns = found.get("turns") or []
    if not turns:
        return "No turns recorded for this thread yet."
    newest = turns[0]
    lines = [
        f"Context sent, newest turn ({newest.get('observed_at', '')}, "
        f"{newest.get('provider') or 'unknown provider'}):"
    ]
    if newest.get("resumed"):
        lines.append("Resumed session: parts marked 'kept' were not sent again.")
    lines.extend(_parts_lines(newest))
    if len(turns) > 1:
        lines.append("Earlier turns:")
        lines.extend(
            f"  {turn.get('observed_at', '')}  {turn['sent_chars']:>8,} chars  "
            f"{_approx(turn['sent_tokens_estimate'])} tokens sent"
            for turn in turns[1:]
        )
    lines.append(str(found.get("estimate") or ""))
    return "\n".join(line for line in lines if line)


def _attempt_note(call: dict[str, Any]) -> str:
    """Why a call was asked, when it wasn't the first ask, and whether it failed."""
    notes = []
    if call.get("attempt") not in (None, "first"):
        notes.append(str(call.get("attempt_label") or call.get("attempt")).lower())
    if call.get("failed"):
        notes.append("failed")
    return f"  ({', '.join(notes)})" if notes else ""


def _kinds_lines(kinds: list[dict[str, Any]]) -> list[str]:
    """Each call kind's calls, re-asks and failures as aligned lines, then per rules version."""
    if not kinds:
        return []
    width = max(len(str(kind.get("label") or kind.get("call"))) for kind in kinds)
    lines = ["Calls by kind (re-asked: asked again because the answer couldn't be used):"]
    for kind in kinds:
        lines.append(
            f"  {str(kind.get('label') or kind.get('call')):<{width}}  "
            f"{int(kind.get('calls') or 0):>6,} calls  "
            f"{int(kind.get('reasked') or 0):>5,} re-asked  "
            f"{int(kind.get('failed') or 0):>5,} failed"
        )
        lines.extend(_version_lines(kind.get("versions")))
    return lines


def _version_lines(versions: Any) -> list[str]:
    """A kind's counts per fixed rules version, newest first; none before versions were kept."""
    found = [v for v in versions or [] if isinstance(v, dict) and v.get("version")]
    return [
        f"    rules {version['version']}  since {str(version.get('first_seen') or '')[:10]}"
        f"{' (current)' if version.get('current') else ''}  "
        f"{int(version.get('calls') or 0):,} calls  "
        f"{int(version.get('re_asked') or 0):,} re-asked  "
        f"{int(version.get('failed') or 0):,} failed"
        for version in found
    ]


def _calls_text(found: dict[str, Any]) -> str:
    """Rem's newest own call, part by part, the earlier calls' totals, then each kind."""
    calls = found.get("calls") or []
    if not calls:
        return "No Rem calls recorded yet."
    newest = calls[0]
    lines = [
        f"Rem's newest call: {newest.get('call_label') or newest.get('call')} "
        f"({newest.get('observed_at', '')}, {newest.get('provider') or 'unknown provider'})"
        f"{_attempt_note(newest)}:"
    ]
    lines.extend(_parts_lines(newest))
    if len(calls) > 1:
        lines.append("Earlier calls:")
        width = max(len(str(call.get("call_label") or "")) for call in calls[1:])
        lines.extend(
            f"  {call.get('observed_at', '')}  {str(call.get('call_label') or ''):<{width}}  "
            f"{call['sent_chars']:>8,} chars  {_approx(call['sent_tokens_estimate'])} tokens"
            f"{_attempt_note(call)}"
            for call in calls[1:]
        )
    lines.extend(_kinds_lines([k for k in found.get("by_kind") or [] if isinstance(k, dict)]))
    lines.append(str(found.get("estimate") or ""))
    return "\n".join(line for line in lines if line)


def context(
    thread_id: str | None = typer.Argument(
        None, help="The thread's ID. Leave it out for Rem's own model calls"
    ),
    turns: int = typer.Option(
        5, "--turns", "-n", min=1, max=100, help="The newest N turns or calls"
    ),
    call: str | None = typer.Option(
        None,
        "--call",
        help="Rem's own calls of one kind: chat_reply, worker_answer, conversation, "
        "task_update, self_work_plan, self_work_fit, self_work_stop, objective_ideas or "
        "organization "
        "(self_work for the records from before self-work's kinds were split)",
    ),
    run_id: str | None = typer.Option(None, "--run", help="Rem's own calls about one task run"),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show how big each part of a prompt was: a thread's turns, or Rem's own calls.

    Sizes only, no text. Without THREAD_ID it lists Rem's own tool-free calls: chat
    replies, answers to workers, conversation decisions, task updates, self-work's
    objective planning, objective checks and stop checks, and organization request
    reviews, with each kind's calls, re-asks and failures.
    """

    def body(client: ApiClient, out: Output) -> CommandResult:
        if thread_id is None:
            query: dict[str, Any] = {"limit": turns}
            if call:
                query["call"] = call
            if run_id:
                query["run"] = run_id
            calls: dict[str, Any] = client.get("/rem/calls", query=query)
            return CommandResult(data=calls, text=_calls_text(calls))
        if call or run_id:
            raise CliError(
                "--call and --run are for Rem's own calls; leave out THREAD_ID.",
                code="invalid_request",
                exit_code=ExitCode.USAGE,
            )
        found: dict[str, Any] = client.get(
            f"/rem/threads/{backend.segment(thread_id)}/context", query={"limit": turns}
        )
        return CommandResult(data=found, text=_context_text(found))

    backend.run("rem context", output, (endpoint, token_file, data_dir), body)


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
    decision = notice.get("decision") if notice.get("kind") == "decision" else None
    if isinstance(decision, dict):
        # needs_you: Rem has a question: Which branch …? (D-7, Raticode; Rem recommends dev)
        where = ", ".join(p for p in (decision.get("id"), notice.get("project_name")) if p)
        tail = f"; Rem recommends {decision['recommended']}" if decision.get("recommended") else ""
        return f"{notice['reason']}: {notice.get('body', '')} ({where}{tail})"
    return f"{notice['reason']}: {notice.get('title') or notice.get('body', '')}"


def _dropped_notice(count: int) -> dict[str, Any]:
    """An event saying workflow notices were removed before the read reached them."""
    plural = "message was" if count == 1 else "messages were"
    return {
        "kind": "dropped",
        "reason": "workflow",
        "dropped": count,
        "title": f"{count} workflow {plural} removed before this read",
    }


def notices(
    cursor: str = typer.Option(
        "", "--cursor", help="The cursor from the last read; without it, only the cursor"
    ),
    include: str = typer.Option(
        "",
        "--include",
        help="Comma list of needs_you, finished, proposals, workflow (default: all)",
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

    workflow alerts are the notices notification nodes with channel: phone stored for
    the phone; each names its workflow_id and run_id. A run past 20 notices has one
    notice with more_from_run set instead, and dropped counts workflow notices removed
    from the computer's store (it keeps the newest 500) before this read reached them.

    A question of Rem's that waits for you while no task stops for it (auto mode) is
    one needs_you alert of kind decision, with its decision id, question, recommended
    answer and project_name.

    The first read without --cursor returns no alerts, only the cursor to start from, so
    a new script isn't flooded with history. Use `rem tasks list --needs-you` for what is
    waiting right now.
    """

    def body(client: ApiClient, out: Output) -> CommandResult:
        if not watch:
            found = client.get("/rem/notices", query={"cursor": cursor, "include": include})
            gone = int(found.get("dropped") or 0)
            shown = [*([_dropped_notice(gone)] if gone else []), *found["notices"]]
            lines = [*map(_notice_line, shown), f"cursor {found['cursor']}"]
            return CommandResult(data=found, text="\n".join(lines))
        position = cursor

        def read() -> tuple[list[Any], bool]:
            nonlocal position
            found = client.get("/rem/notices", query={"cursor": position, "include": include})
            position = found["cursor"]
            gone = int(found.get("dropped") or 0)
            events = [*([_dropped_notice(gone)] if gone else []), *found["notices"]]
            return [{**n, "cursor": position} for n in events], bool(found["more"])

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
            write_private_text(file, text)
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
    lines = [
        f"{m['id']}  {m['text']}"
        + (f"  [{m.get('project_name') or m['project']}]" if m.get("project") else "")
        + (
            f"  ({'ended' if m.get('ended') else 'until'} {str(m['until'])[:10]})"
            if m.get("until")
            else ""
        )
        for m in data.get("memories", [])
    ]
    return CommandResult(data=data, text="\n".join([f"Memory is {data.get('mode')}.", *lines]))


_MEMORY_PROJECT = typer.Option(
    None,
    "--project",
    help="Root folder of an open project the memory belongs to; Rem uses it only for work there",
)
_MEMORY_UNTIL = typer.Option(
    None,
    "--until",
    help=(
        "Last day the memory applies (YYYY-MM-DD, or a date and time with its timezone); "
        "after it Rem stops using it but keeps it listed"
    ),
)


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
    project: str | None = _MEMORY_PROJECT,
    until: str | None = _MEMORY_UNTIL,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Remember a fact, for every project or one. Text that looks like a key is refused."""
    body: dict[str, Any] = {
        "text": text,
        **({"project": project} if project else {}),
        **({"until": until} if until else {}),
    }
    backend.run(
        "rem memories add",
        output,
        (endpoint, token_file, data_dir),
        lambda client, out: _memories(client.post("/rem/memories", body)),
    )


@memories_app.command("edit")
def memories_edit(
    memory_id: str = typer.Argument(...),
    text: str | None = typer.Argument(None, help="The corrected text; leave out to keep it"),
    project: str | None = _MEMORY_PROJECT,
    every_project: bool = typer.Option(
        False, "--global", help="Make the memory apply to every project again"
    ),
    until: str | None = _MEMORY_UNTIL,
    keep: bool = typer.Option(
        False, "--keep", help="Remove the memory's end date, so an ended memory applies again"
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Correct one remembered fact, move it to a project or every project, or set its end."""
    body: dict[str, Any] = {} if text is None else {"text": text}
    if project or every_project:
        body["project"] = project or None
    if until or keep:
        body["until"] = until or None

    def edit(client: ApiClient, out: Output) -> CommandResult:
        if (project and every_project) or (until and keep):
            raise CliError(
                "Use --project or --global, and --until or --keep, not both of a pair.",
                code="invalid_input",
                exit_code=ExitCode.USAGE,
            )
        if not body:
            raise CliError(
                "Give the corrected text, --project, --global, --until or --keep.",
                code="invalid_input",
                exit_code=ExitCode.USAGE,
            )
        return _memories(client.patch(f"/rem/memories/{backend.segment(memory_id)}", body))

    backend.run("rem memories edit", output, (endpoint, token_file, data_dir), edit)


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


# Decisions ------------------------------------------------------------------------

_DECISION_STATUS = {
    "pending": "waiting for you",
    "queued": "queued behind the questions waiting for you",
    "provisional": "Rem decided (auto mode)",
    "confirmed": "decided",
    "superseded": "replaced",
    "rejected": "turned down",
}


def _decision_status(item: dict[str, Any]) -> str:
    if item.get("status") == "rejected" and item.get("retired_at"):
        return "retired"
    status = str(item["status"])
    return _DECISION_STATUS.get(status, status)


def _decision_line(item: dict[str, Any]) -> str:
    answer = item.get("answer") or item.get("recommended") or ""
    where = f"  [{item.get('project_name') or item['project']}]" if item.get("project") else ""
    return (
        f"{item['id']}  {_decision_status(item)}: "
        f"{item['question']} -> {answer or '(no answer yet)'}{where}"
    )


def _edit_lines(edit: dict[str, Any]) -> list[str]:
    verb = "Replace" if edit.get("action") == "replace" else "Change"
    lines = [
        f"{edit.get('id')} waits for your OK: {verb} {edit.get('decision')} "
        f"({edit.get('question') or edit.get('topic') or ''})"
    ]
    for part in edit.get("changes") or []:
        lines.append(f"  {part.get('label')}: {part.get('before') or '(empty)'}")
        lines.append(f"  {' ' * len(str(part.get('label')))}  -> {part.get('after') or '(empty)'}")
    return lines


def _counted_path(kind: str, glob: str, counted: dict[str, Any]) -> str:
    """A record path with its tracked-file count: ``docs/** (12 files)``."""
    from gofer.ui.rem_decision_records import count_note

    if kind not in {"applies_to", "forbids"}:
        return glob
    note = count_note(kind, (counted.get(kind) or {}).get(glob))
    return f"{glob} ({note})" if note else glob


def _decision_lines(item: dict[str, Any]) -> list[str]:
    """The decision's line, and under a waiting one who asked it and what waits on it."""
    from gofer.ui.rem_decisions import asked_line

    asked = asked_line(item.get("asked"))
    return [_decision_line(item), *([f"      {asked}"] if asked else [])]


def _decisions(data: dict[str, Any]) -> CommandResult:
    lines = [line for item in data.get("items", []) for line in _decision_lines(item)]
    queued = f" ({data['queued']} more queued)" if data.get("queued") else ""
    head = f"{data.get('waiting', 0)} waiting{queued}, {data.get('confirmed', 0)} decided."
    edits = [line for edit in data.get("edits") or [] for line in _edit_lines(edit)]
    return CommandResult(data=data, text="\n".join([head, *edits, *lines]))


def _change_decision(
    command: str,
    body: dict[str, Any],
    output: opts.OutputFormat,
    connection: tuple[str | None, Path | None, Path | None],
) -> None:
    backend.run(
        command,
        output,
        connection,
        lambda client, out: _changed(body, client.post("/rem/decisions", body)),
    )


def _changed(body: dict[str, Any], data: dict[str, Any]) -> CommandResult:
    """The list after a change, led by what forget or retire did to a written record."""
    result = _decisions(data)
    if body.get("action") not in {"forget", "retire"}:
        return result
    item = next((i for i in data.get("items", []) if i.get("id") == body.get("id")), None)
    if item is None:
        lead = f"Forgot {body.get('id')}."
    else:
        path = item.get("record_path") or "its file"
        lead = (
            f"Retired {item['id']}: workers stop getting it, and {path} stays in the "
            "repository marked status: rejected, committed on the landing branch."
        )
    return CommandResult(data=result.data, text=f"{lead}\n{result.text}")


_CONTEXT = typer.Option(None, "--context", help="Record: what forced the choice")
_OPTIONS = typer.Option(None, "--options", help="Record: the options not chosen, and why")
_CONSEQUENCES = typer.Option(
    None, "--consequences", help="Record: what gets easier and what gets harder"
)
_CHECK = typer.Option(None, "--check", help="Record: how the decision is checked")
_APPLIES = typer.Option(
    None, "--applies-to", help="Record: a path glob it is about (repeat for more)"
)
_FORBIDS = typer.Option(
    None, "--forbids", help="Record: a path glob a change must never add (repeat for more)"
)
_SIGNALS = typer.Option(
    None, "--signal", help="Record: a word that flags a contradicting proposal (repeat)"
)
_NOTE = typer.Option(None, "--note", help="One line on why, kept in its history")


def _record_body(
    body: dict[str, Any],
    *,
    context: str | None,
    options: str | None,
    consequences: str | None,
    check: str | None,
    applies_to: list[str] | None,
    forbids: list[str] | None,
    signals: list[str] | None,
    note: str | None = None,
) -> dict[str, Any]:
    for key, value in (
        ("context", context),
        ("options", options),
        ("consequences", consequences),
        ("check", check),
        ("applies_to", applies_to),
        ("forbids", forbids),
        ("signals", signals),
        ("note", note),
    ):
        if value is not None:
            body[key] = value
    return body


_DECISION_PROJECT = typer.Option(
    None, "--project", help="Only decisions for this project root folder"
)
_EXPECTED = typer.Option(
    None,
    "--expected-revision",
    help="Refuse the change if the decisions changed since this revision",
)


@decisions_app.command("list")
def decisions_list(
    project: str | None = _DECISION_PROJECT,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List decisions: waiting for you first, then in force, then replaced or turned down."""
    path = "/rem/decisions" + (f"?project={quote(project, safe='')}" if project else "")
    backend.run(
        "rem decisions list",
        output,
        (endpoint, token_file, data_dir),
        lambda client, out: _decisions(client.get(path)),
    )


@decisions_app.command("show")
def decisions_show(
    decision_id: str = typer.Argument(..., help="The decision's ID, such as D-3"),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show one decision with its evidence, other options and history."""

    def show(client: ApiClient, out: Output) -> CommandResult:
        data = client.get("/rem/decisions")
        item = next((i for i in data.get("items", []) if i.get("id") == decision_id), None)
        if item is None:
            raise CliError(
                f"No decision {decision_id}.", code="not_found", exit_code=ExitCode.NOT_FOUND
            )
        from gofer.ui.rem_decisions import decided_line

        lines = [_decision_line(item)]
        if decided_line(item):
            lines.append(decided_line(item))
        if item.get("why"):
            lines.append(f"Why: {item['why']}")
        for evidence in item.get("evidence") or []:
            lines.append(f"Evidence: {evidence.get('kind')} {evidence.get('ref')}")
        for other in item.get("alternatives") or []:
            lines.append(f"Other option: {other.get('answer')} ({other.get('why_not') or ''})")
        record = item.get("record") if item.get("remember_as") == "record" else None
        if record is not None:
            lines.append(f"Record: {item.get('record_path') or 'not in the repository yet'}")
            if item.get("record_error"):
                lines.append(f"Not written yet: {item['record_error']}")
            if item.get("edited_outside"):
                lines.append("Edited outside Raticode.")
            counted = item.get("path_counts") or {}
            for key, label in (
                ("applies_to", "Applies to"),
                ("forbids", "Never"),
                ("signals", "Signals"),
            ):
                if record.get(key):
                    shown = [_counted_path(key, entry, counted) for entry in record[key]]
                    lines.append(f"{label}: {', '.join(shown)}")
            if counted.get("branch"):
                at = counted.get("commit", "")
                lines.append(f"Paths counted on {counted['branch']} at {at}.")
            for key, label in (
                ("context", "Context"),
                ("options", "Options we didn't choose"),
                ("consequences", "Consequences"),
                ("check", "How we check it"),
            ):
                if record.get(key):
                    lines.append(f"{label}: {record[key]}")
        if item.get("supersedes_all"):
            replaced = ", ".join(item["supersedes_all"])
            lines.append(f"Conflict: {item.get('conflict', '')} Replaces {replaced}.")
        check = item.get("last_check") or {}
        if check:
            state = "passed" if check.get("passed") else f"failed (exit {check.get('exit_code')})"
            lines.append(f"Raticode's last run of it: {state} at {check.get('ran_at', '')}")
        return CommandResult(data=item, text="\n".join(lines))

    backend.run("rem decisions show", output, (endpoint, token_file, data_dir), show)


@decisions_app.command("use")
def decisions_use(
    decision_id: str = typer.Argument(..., help="The waiting decision's ID"),
    expected_revision: int | None = _EXPECTED,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Use Rem's recommendation. Work that waited on it continues."""
    body: dict[str, Any] = {"action": "use", "id": decision_id}
    if expected_revision is not None:
        body["expected_revision"] = expected_revision
    _change_decision("rem decisions use", body, output, (endpoint, token_file, data_dir))


@decisions_app.command("change")
def decisions_change(
    decision_id: str = typer.Argument(...),
    answer: str = typer.Argument(..., help="Your answer instead of Rem's recommendation"),
    expected_revision: int | None = _EXPECTED,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Decide with your own answer; it replaces any decision in force on the same topic."""
    body: dict[str, Any] = {"action": "change", "id": decision_id, "answer": answer}
    if expected_revision is not None:
        body["expected_revision"] = expected_revision
    _change_decision("rem decisions change", body, output, (endpoint, token_file, data_dir))


@decisions_app.command("edit")
def decisions_edit(
    decision_id: str = typer.Argument(...),
    answer: str | None = typer.Option(None, "--answer", help="The corrected answer"),
    question: str | None = typer.Option(None, "--question", help="The corrected question"),
    why: str | None = typer.Option(None, "--why", help="The corrected reason"),
    context: str | None = _CONTEXT,
    options: str | None = _OPTIONS,
    consequences: str | None = _CONSEQUENCES,
    check: str | None = _CHECK,
    applies_to: list[str] | None = _APPLIES,
    forbids: list[str] | None = _FORBIDS,
    signals: list[str] | None = _SIGNALS,
    note: str | None = _NOTE,
    expected_revision: int | None = _EXPECTED,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Correct a decision's wording, or a record's parts, without changing its status."""
    body: dict[str, Any] = {"action": "edit", "id": decision_id}
    for key, value in (("answer", answer), ("question", question), ("why", why)):
        if value is not None:
            body[key] = value
    _record_body(
        body,
        context=context,
        options=options,
        consequences=consequences,
        check=check,
        applies_to=applies_to,
        forbids=forbids,
        signals=signals,
    )
    if len(body) == 2:
        raise CliError(
            "Give --answer, --question, --why or a record part.",
            code="invalid_input",
            exit_code=ExitCode.USAGE,
        )
    if note is not None:
        body["note"] = note
    if expected_revision is not None:
        body["expected_revision"] = expected_revision
    _change_decision("rem decisions edit", body, output, (endpoint, token_file, data_dir))


@decisions_app.command("replace")
def decisions_replace(
    decision_id: str = typer.Argument(..., help="The decision in force to replace"),
    answer: str = typer.Argument(..., help="The new decision"),
    question: str | None = typer.Option(None, "--question", help="A new title or question"),
    why: str | None = typer.Option(None, "--why", help="Why it changed"),
    context: str | None = _CONTEXT,
    options: str | None = _OPTIONS,
    consequences: str | None = _CONSEQUENCES,
    check: str | None = _CHECK,
    applies_to: list[str] | None = _APPLIES,
    forbids: list[str] | None = _FORBIDS,
    signals: list[str] | None = _SIGNALS,
    note: str | None = _NOTE,
    expected_revision: int | None = _EXPECTED,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Replace a decision with a new one; the old one is kept as replaced, linked both ways.

    A record keeps its parts unless you give new ones, and its file is updated in the
    project's repository.
    """
    body: dict[str, Any] = {"action": "replace", "id": decision_id, "answer": answer}
    if question:
        body["question"] = question
    if why:
        body["why"] = why
    _record_body(
        body,
        context=context,
        options=options,
        consequences=consequences,
        check=check,
        applies_to=applies_to,
        forbids=forbids,
        signals=signals,
        note=note,
    )
    if expected_revision is not None:
        body["expected_revision"] = expected_revision
    _change_decision("rem decisions replace", body, output, (endpoint, token_file, data_dir))


def _simple(action: str, help_text: str) -> Callable[..., None]:
    def command(
        decision_id: str = typer.Argument(...),
        expected_revision: int | None = _EXPECTED,
        output: opts.Output = opts.OutputFormat.json,
        endpoint: opts.EndpointUrl = None,
        token_file: opts.TokenFile = None,
        data_dir: opts.DataDir = None,
    ) -> None:
        body: dict[str, Any] = {"action": action, "id": decision_id}
        if expected_revision is not None:
            body["expected_revision"] = expected_revision
        _change_decision(f"rem decisions {action}", body, output, (endpoint, token_file, data_dir))

    command.__doc__ = help_text
    command.__name__ = f"decisions_{action}"
    return command


decisions_app.command("reject")(
    _simple("reject", "Turn down a waiting decision. Work that waited on it skips that part.")
)
decisions_app.command("reopen")(
    _simple(
        "reopen",
        "Ask a decided question again: a new card waits for you, and the decision stays in "
        "force until you answer it.",
    )
)
decisions_app.command("forget")(
    _simple(
        "forget",
        "Delete a decision from the record. A record whose file is in the repository is "
        "refused: retire it instead.",
    )
)
decisions_app.command("retire")(
    _simple(
        "retire",
        "Retire a decision record whose file is in the repository: workers stop getting it, "
        "it stays turned down, and its file is rewritten with status: rejected and committed "
        "on the landing branch.",
    )
)


def _settle(action: str, help_text: str) -> Callable[..., None]:
    def command(
        edit_id: str = typer.Argument(..., help="The change's ID, such as E-2"),
        output: opts.Output = opts.OutputFormat.json,
        endpoint: opts.EndpointUrl = None,
        token_file: opts.TokenFile = None,
        data_dir: opts.DataDir = None,
    ) -> None:
        name = action.replace("_", "-")
        body: dict[str, Any] = {"action": action, "id": edit_id}
        _change_decision(f"rem decisions {name}", body, output, (endpoint, token_file, data_dir))

    command.__doc__ = help_text
    command.__name__ = f"decisions_{action}"
    return command


decisions_app.command("confirm-edit")(
    _settle(
        "confirm_edit",
        "Press OK on a change you asked Rem for in chat: the decision changes, and so does "
        "its record file. Refused if the decision changed since Rem showed it.",
    )
)
decisions_app.command("cancel-edit")(
    _settle("cancel_edit", "Press Cancel on a change you asked Rem for in chat: nothing changes.")
)


@decisions_app.command("add")
def decisions_add(
    topic: str = typer.Argument(..., help="A short key, such as checks.fast"),
    answer: str = typer.Argument(..., help="The decision"),
    question: str | None = typer.Option(None, "--question", help="The question it answers"),
    project: str | None = typer.Option(
        None, "--project", help="Project root it applies to; leave out for one about you"
    ),
    record: bool = typer.Option(
        False,
        "--record",
        help="Keep it as a decision record in the project's repository (needs --project)",
    ),
    context: str | None = _CONTEXT,
    options: str | None = _OPTIONS,
    consequences: str | None = _CONSEQUENCES,
    check: str | None = _CHECK,
    applies_to: list[str] | None = _APPLIES,
    forbids: list[str] | None = _FORBIDS,
    signals: list[str] | None = _SIGNALS,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Record a decision yourself; it replaces any decision in force on the same topic."""
    body: dict[str, Any] = {"action": "add", "topic": topic, "answer": answer}
    if question:
        body["question"] = question
    if project:
        body["project"] = str(Path(project).expanduser().resolve())
    if record:
        if not project:
            raise CliError(
                "A decision record belongs to a project; give --project.",
                code="invalid_input",
                exit_code=ExitCode.USAGE,
            )
        body["remember_as"] = "record"
        _record_body(
            body,
            context=context,
            options=options,
            consequences=consequences,
            check=check,
            applies_to=applies_to,
            forbids=forbids,
            signals=signals,
        )
    _change_decision("rem decisions add", body, output, (endpoint, token_file, data_dir))


@decisions_app.command("paths")
def decisions_paths(
    applies_to: list[str] | None = _APPLIES,
    forbids: list[str] | None = _FORBIDS,
    project: str | None = typer.Option(
        None, "--project", help="Project root to count in, for a record you're about to add"
    ),
    decision_id: str | None = typer.Option(
        None, "--id", help="Count in this decision's project, for an edit or replacement"
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Count the tracked files record paths match on the project's landing branch.

    Shows what an Applies to or Never touch path covers before you save it. A path that
    matches no files, or a Never touch path that matches files, is a warning, not an error:
    a path can be planned. Nothing changes.
    """
    if bool(project) == bool(decision_id):
        raise CliError("Give --project or --id.", code="invalid_input", exit_code=ExitCode.USAGE)
    if not applies_to and not forbids:
        raise CliError(
            "Give --applies-to or --forbids.", code="invalid_input", exit_code=ExitCode.USAGE
        )
    body: dict[str, Any] = {"applies_to": applies_to or [], "forbids": forbids or []}
    if project:
        body["project"] = str(Path(project).expanduser().resolve())
    else:
        body["id"] = decision_id

    def count(client: ApiClient, out: Output) -> CommandResult:
        data = client.post("/rem/decisions/paths", body)
        counted = data.get("path_counts")
        if not counted:
            return CommandResult(data=data, text=data.get("reason") or "Paths can't be counted.")
        lines = [f"Counted on {counted.get('branch')} at {counted.get('commit', '')}."]
        for key, label in (("applies_to", "Applies to"), ("forbids", "Never")):
            for glob in counted.get(key) or {}:
                lines.append(f"{label}: {_counted_path(key, glob, counted)}")
        return CommandResult(data=data, text="\n".join(lines))

    backend.run("rem decisions paths", output, (endpoint, token_file, data_dir), count)


_SCAN_OUTCOMES = {
    "pending": "waiting for you",
    "queued": "queued behind the questions waiting for you",
    "provisional": "Rem decided it (auto mode)",
    "waiting": "already waiting for you",
    "answered": "already decided",
    "rechecked": "already decided; evidence refreshed",
    "skipped": "already recorded",
    "turned down before": "you turned this down before",
    "unchecked": "its evidence couldn't be checked",
}


def _scan_result(data: dict[str, Any]) -> CommandResult:
    scan = data.get("scan") or {}
    outcomes = {o.get("topic"): o for o in data.get("outcomes") or []}
    read = ", ".join(scan.get("read") or []) or "no docs or CI files"
    lines = [f"Scanned {scan.get('project', '')} (read: {read})."]
    for topic, candidates in (scan.get("topics") or {}).items():
        if not candidates:
            lines.append(f"{topic}: nothing found")
            continue
        best = candidates[0]
        where = "; ".join(f"{s['label']} ({s['ref']})" for s in best.get("sources", [])[:3])
        outcome = outcomes.get(topic)
        state = (
            f" [{_SCAN_OUTCOMES.get(outcome['outcome'], outcome['outcome'])}"
            + (f" {outcome['id']}]" if outcome.get("id") else "]")
            if outcome
            else ""
        )
        lines.append(f"{topic}: {best['answer']}{state}")
        lines.append(f"  found in: {where}")
        for other in candidates[1:]:
            lines.append(f"  also: {other['answer']}")
    return CommandResult(data=data, text="\n".join(lines))


@decisions_app.command("scan")
def decisions_scan(
    project: str = typer.Argument(..., help="The project's root folder"),
    preview: bool = typer.Option(
        False, "--preview", help="Show what the scan finds without recording anything"
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Find the project's check command, landing branch and decision folder in its files.

    Reads CI files, agent docs, package and build files and git history; no model is asked
    and nothing runs. What it finds becomes decisions waiting for you, or Rem's
    provisional ones while self-work's auto mode is on. A decision in force never changes.
    """
    body: dict[str, Any] = {"project": str(Path(project).expanduser().resolve())}
    if preview:
        body["preview"] = True
    backend.run(
        "rem decisions scan",
        output,
        (endpoint, token_file, data_dir),
        lambda client, out: _scan_result(client.post("/rem/decisions/scan", body)),
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
    """One task with its prompt; task lists leave prompts out."""
    try:
        return dict(client.get(f"/rem/tasks/{backend.segment(identity)}")["task"])
    except CliError as exc:
        if exc.code != "not_found":
            raise
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
    """List Rem's tasks with their latest run, without prompts (show has them)."""

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
    """Show one task, its prompt, revision and recent runs."""

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
    provider: str | None = typer.Option(
        None, "--provider", help="Provider for the task's thread, such as claude_code"
    ),
    model: str | None = typer.Option(None, "--model", help="Model for the task's thread"),
    account: str | None = typer.Option(
        None,
        "--account",
        help="Run the task's thread on this account of its provider "
        "(see `provider account list`), whatever Rem's account setting says",
    ),
    computer: str | None = typer.Option(
        None,
        "--computer",
        help="Run the task only on this computer, by its ID from `computers list`. A paired "
        "desktop runs research and conversation tasks only, in its matching project",
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
        for key, value in (
            ("provider", provider),
            ("model", model),
            ("provider_profile", account),
            ("computer", computer),
        ):
            if value:
                fields[key] = value
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


@tasks_app.command("make-workflow")
def tasks_make_workflow(
    task_id: str = typer.Argument(..., help="A Daily, Weekly or Custom cron task"),
    request: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Start a Rem task that drafts a workflow from a repeating task that passed twice.

    The draft is checked, preflighted and tested once. Nothing switches until you run
    `rem tasks switch-over` on it or press Switch over in the app.
    """
    identity = backend.request_id(request)

    def body(client: ApiClient, out: Output) -> CommandResult:
        started = client.post(
            f"/rem/tasks/{backend.segment(task_id)}/workflow",
            {"action": "draft"},
            request_id=identity,
        )
        return CommandResult(
            data=started,
            text=f"Drafting: {started['draft']['title']} (task {started['draft']['id']}).",
        )

    backend.run(
        "rem tasks make-workflow",
        output,
        (endpoint, token_file, data_dir),
        body,
        request_id=identity,
    )


@tasks_app.command("switch-over")
def tasks_switch_over(
    task_id: str = typer.Argument(..., help="The repeating task, or its drafting task"),
    yes: opts.Yes = False,
    unseen: bool = typer.Option(
        False,
        "--unseen",
        help="Also approve files the diff couldn't show in full and MCP servers whose "
        "package has no exact version",
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Run a repeating task's drafted workflow on its schedule instead of the task.

    Prints the workflow files and the cron trigger it would add. With --yes it adds the
    trigger, approves exactly the version shown and pauses the task's schedule.
    `rem tasks resume` undoes it. Only you can do this: inside an agent turn it is
    refused (exit 5).
    """
    from gofer.rattish.trigger_approval import unified_diff_text, unseen_message

    def body(client: ApiClient, out: Output) -> CommandResult:
        backend.require_user("switch a task over to a workflow")
        path = f"/rem/tasks/{backend.segment(task_id)}/workflow"
        shown = client.post(path, {"action": "preview"})
        changes = shown["changes"]
        lines = [
            f"Workflow: {shown['workflow_path']}",
            f"Trigger: cron {shown['cron']} ({shown['timezone']}), the task's {shown['schedule']}",
            unified_diff_text(changes),
        ]
        if not yes:
            raise CliError(
                "Add --yes to add the trigger, approve this version and pause the task.",
                code="confirmation_required",
                exit_code=ExitCode.USAGE,
                data=shown,
            )
        if changes.get("unseen") and not unseen:
            raise CliError(
                unseen_message(changes["unseen"]).replace(
                    "then confirm that you approve", "then add --unseen to approve"
                ),
                code="unseen_changes",
                exit_code=ExitCode.USAGE,
                data=shown,
            )
        switched = client.post(
            path,
            {
                "action": "switch",
                "fingerprint": changes["fingerprint"],
                "accept_unseen": bool(unseen),
            },
        )
        lines.append(
            f"Switched over: trigger {switched['trigger_id']} runs the workflow, and the "
            "task's schedule is paused."
        )
        return CommandResult(data={**switched, "preview": shown}, text="\n".join(lines))

    backend.run("rem tasks switch-over", output, (endpoint, token_file, data_dir), body)


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


def _provider_lines(run: dict[str, Any]) -> list[str]:
    """Which provider app versions the run's turns started, and any change since it passed."""
    from gofer.core.provider_versions import rem_turn_lines

    turns = [turn for turn in run.get("turns") or [] if isinstance(turn, dict)]
    changes = [
        str(item["text"])
        for item in run.get("provider_version_changes") or []
        if isinstance(item, dict) and item.get("text")
    ]
    return [*rem_turn_lines(turns), *changes]


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
        lines = [f"Run {run['id']} {run.get('state')}."]
        if run.get("state") == "queued" and run.get("waiting_reason"):
            lines.append(f"Waiting: {run['waiting_reason']}")
        suggestion = run.get("suggestion")
        if isinstance(suggestion, dict) and suggestion.get("text"):
            # A paired desktop with room; nothing goes there until you pick it.
            lines.append(f"{suggestion['text']} (Raticode rem runs run-there {run['id']})")
        lines += _provider_lines(run)
        return CommandResult(data=run, text="\n".join(lines))

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


@runs_app.command("acknowledge-all")
def runs_acknowledge_all(
    request: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Dismiss every task update at once; tasks still waiting for your answer stay."""
    connection = (endpoint, token_file, data_dir)
    identity = backend.request_id(request)

    def body(client: ApiClient, out: Output) -> CommandResult:
        result = client.post("/rem/runs/acknowledge", {}, request_id=identity)
        count = int(result.get("count") or 0)
        waiting = int(result.get("needs_you") or 0)
        text = f"Dismissed {count} task {'update' if count == 1 else 'updates'}."
        if waiting:
            text += f" {waiting} still {'needs' if waiting == 1 else 'need'} you."
        return CommandResult(data=result, text=text)

    backend.run("rem runs acknowledge-all", output, connection, body, request_id=identity)


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


@runs_app.command("run-there")
def runs_run_there(
    run_id: str = typer.Argument(...),
    computer: str | None = typer.Option(
        None, "--computer", help="The paired desktop's ID; defaults to the suggested one."
    ),
    request: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Run a waiting task on the paired desktop its suggestion names (Run there).

    Only a research or conversation task waiting for room, when a paired desktop has
    room for it. It counts as your own start.
    """
    identity = backend.request_id(request)
    payload = {"computer_id": computer} if computer else {}

    def body(client: ApiClient, out: Output) -> CommandResult:
        changed = client.post(
            f"/rem/runs/{backend.segment(run_id)}/run_there", payload, request_id=identity
        )
        name = ((changed.get("run") or {}).get("run_there") or {}).get("computer_name")
        return CommandResult(data=changed, text=f"Run {run_id} starts on {name or computer}.")

    backend.run(
        "rem runs run-there", output, (endpoint, token_file, data_dir), body, request_id=identity
    )


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
