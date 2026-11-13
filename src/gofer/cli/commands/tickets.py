"""The global Kanban board (Rem's tickets and every organization's) and Rem's self-work.

Every change carries the ticket's revision, so a script never overwrites a change it
hasn't seen (exit 4 on a conflict). The board's own rules apply: a proposal must be
approved before it moves, a running ticket can't be edited, and an organization's
tickets go to its workers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from gofer.cli import client_options as opts
from gofer.cli.client import ApiClient, CommandResult, Output
from gofer.cli.commands import _backend as backend

app = typer.Typer(
    help="Global Kanban tickets: Rem's and every organization's. Needs a running backend.",
    no_args_is_help=True,
)
selfwork_app = typer.Typer(
    help="Rem's self-work: status, report and settings. Needs a running backend.",
    no_args_is_help=True,
)

COLUMNS = ("backlog", "todo", "in_progress", "done", "cancelled")
SELF_WORK_SETTINGS = ("enabled", "max_threads", "auto_pull_backlog")
_ORG = typer.Option(None, "--org", help="Organization ID, for an organization's ticket")
_REVISION = typer.Option(..., "--expected-revision", help="The ticket's revision from `show`")


def _board(client: ApiClient) -> dict[str, Any]:
    return dict(client.get("/rem/kanban")["board"])


def _address(client: ApiClient, identity: str, organization: str | None) -> dict[str, Any]:
    """The ticket's ID, scope and organization, looked up by ID or key such as REM-12."""
    if organization:
        return {"ticket_id": identity, "scope": "organization", "organization_id": organization}
    for ticket in _board(client)["tickets"]:
        if identity in {ticket["ticket_id"], ticket.get("key")}:
            return {
                "ticket_id": ticket["ticket_id"],
                "scope": ticket["scope"],
                "organization_id": ticket.get("organization_id") or "",
            }
    return {"ticket_id": identity, "scope": "rem", "organization_id": ""}


def _act(
    client: ApiClient,
    payload: dict[str, Any],
    done: str,
    *,
    request_id: str | None = None,
    expected_revision: int | None = None,
) -> CommandResult:
    board = client.post(
        "/rem/kanban/ticket",
        payload,
        request_id=request_id,
        expected_revision=expected_revision,
    )
    return CommandResult(data=board, text=done)


def _line(ticket: dict[str, Any]) -> str:
    where = ticket.get("organization_name") or "Rem"
    flags = (
        " (proposal)" if ticket.get("proposed") else " (running)" if ticket.get("running") else ""
    )
    return (
        f"{ticket.get('key') or ticket['ticket_id']:<10} {ticket['status']:<11} "
        f"r{ticket['revision']:<3} {where}: {ticket['title']}{flags}"
    )


def _comment_line(entry: dict[str, Any]) -> str:
    said = entry.get("title") or entry.get("body", "")
    return f"{entry.get('created_at', '')} {entry.get('author', '')}: {said}"


@app.command("list")
def list_tickets(
    status: list[str] | None = typer.Option(None, "--status", help="Repeat for several"),
    scope: str | None = typer.Option(None, "--scope", help="rem or organization"),
    organization: str | None = _ORG,
    assignee: str | None = typer.Option(None, "--assignee", help="rem or a worker ID"),
    project: Path | None = typer.Option(None, "--project", help="Only this project folder"),
    proposals: bool = typer.Option(False, "--proposals", help="Only proposals awaiting you"),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List tickets: open ones first, then the most recently closed."""

    def body(client: ApiClient, out: Output) -> CommandResult:
        board = _board(client)
        root = str(project.expanduser().resolve()) if project else None
        tickets = [
            t
            for t in board["tickets"]
            if (not status or t["status"] in status)
            and (scope is None or t["scope"] == scope)
            and (organization is None or t.get("organization_id") == organization)
            and (assignee is None or t.get("assignee_id") == assignee)
            and (root is None or t.get("project_dir") == root)
            and (not proposals or t.get("proposed"))
        ]
        data = {
            "tickets": tickets,
            "counts": board["counts"],
            "earlier": board["earlier"],
            "organizations": board["organizations"],
            "participants": board["participants"],
            "projects": board["projects"],
        }
        return CommandResult(data=data, text="\n".join(map(_line, tickets)) or "No tickets.")

    backend.run("tickets list", output, (endpoint, token_file, data_dir), body)


@app.command("show")
def show(
    ticket: str = typer.Argument(..., help="Ticket ID or key, such as REM-12"),
    organization: str | None = _ORG,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show a ticket with its comments and status history."""

    def body(client: ApiClient, out: Output) -> CommandResult:
        timeline = client.post("/rem/kanban/timeline", _address(client, ticket, organization))
        lines = [_line(timeline["ticket"]), "", timeline["ticket"].get("description") or ""]
        lines.extend(map(_comment_line, timeline.get("comments") or []))
        return CommandResult(data=timeline, text="\n".join(lines))

    backend.run("tickets show", output, (endpoint, token_file, data_dir), body)


@app.command("create")
def create(
    title: str = typer.Option(..., "--title"),
    description: str | None = typer.Option(None, "--description", help="Text, or - for stdin"),
    description_file: Path | None = typer.Option(None, "--description-file"),
    priority: str = typer.Option("medium", "--priority", help="low, medium, high or critical"),
    status: str = typer.Option("todo", "--status", help="backlog or todo"),
    kind: str | None = typer.Option(None, "--type", help="research, maintain, develop or errand"),
    project: Path | None = typer.Option(None, "--project", help="Open project folder"),
    done_when: str | None = typer.Option(None, "--done-when", help="How to tell it's finished"),
    organization: str | None = _ORG,
    assignee: str | None = typer.Option(None, "--assignee", help="rem, or a worker ID with --org"),
    request: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Create a ticket for Rem, or with --org for an organization."""
    identity = backend.request_id(request)

    def body(client: ApiClient, out: Output) -> CommandResult:
        payload: dict[str, Any] = {
            "action": "create",
            "title": title,
            "description": backend.read_body(description, description_file, "description"),
            "priority": priority,
            "status": status,
        }
        if organization:
            payload.update(scope="organization", organization_id=organization)
        else:
            payload["scope"] = "rem"
            if kind:
                payload["type"] = kind
            if project:
                payload["project_dir"] = str(project.expanduser().resolve())
            if done_when is not None:
                payload["done_when"] = done_when
        if assignee:
            payload["assignee_id"] = assignee
        return _act(client, payload, f"Created “{title}”.", request_id=identity)

    backend.run(
        "tickets create", output, (endpoint, token_file, data_dir), body, request_id=identity
    )


@app.command("edit")
def edit(
    ticket: str = typer.Argument(...),
    expected_revision: int = _REVISION,
    title: str | None = typer.Option(None, "--title"),
    description: str | None = typer.Option(None, "--description", help="Text, or - for stdin"),
    description_file: Path | None = typer.Option(None, "--description-file"),
    priority: str | None = typer.Option(None, "--priority"),
    kind: str | None = typer.Option(None, "--type"),
    project: Path | None = typer.Option(None, "--project"),
    done_when: str | None = typer.Option(None, "--done-when"),
    organization: str | None = _ORG,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Change a ticket's title, description, priority, type, project or finish line."""

    def body(client: ApiClient, out: Output) -> CommandResult:
        changes: dict[str, Any] = {}
        if title is not None:
            changes["title"] = title
        if description is not None or description_file is not None:
            changes["description"] = backend.read_body(description, description_file, "description")
        if priority is not None:
            changes["priority"] = priority
        if kind is not None:
            changes["type"] = kind
        if project is not None:
            changes["project_dir"] = str(project.expanduser().resolve())
        if done_when is not None:
            changes["done_when"] = done_when
        if not changes:
            raise backend.invalid("Change at least one field.")
        payload = {"action": "edit", **_address(client, ticket, organization), **changes}
        return _act(client, payload, "Saved.", expected_revision=expected_revision)

    backend.run("tickets edit", output, (endpoint, token_file, data_dir), body)


@app.command("move")
def move(
    ticket: str = typer.Argument(...),
    column: str = typer.Argument(..., help="backlog, todo, in_progress, done or cancelled"),
    expected_revision: int = _REVISION,
    organization: str | None = _ORG,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Move a ticket. in_progress starts Rem on it now; done is for you to decide."""

    def body(client: ApiClient, out: Output) -> CommandResult:
        if column not in COLUMNS:
            raise backend.invalid(f"Choose a column: {', '.join(COLUMNS)}.")
        if column == "done":
            backend.require_user("mark a ticket done by hand")
        payload = {"action": "move", **_address(client, ticket, organization), "column": column}
        return _act(client, payload, f"Moved to {column}.", expected_revision=expected_revision)

    backend.run("tickets move", output, (endpoint, token_file, data_dir), body)


@app.command("assign")
def assign(
    ticket: str = typer.Argument(...),
    expected_revision: int = _REVISION,
    assignee: str | None = typer.Option(
        None, "--assignee", help="A worker ID in the ticket's organization, or rem"
    ),
    to_organization: str | None = typer.Option(
        None, "--to-org", help="Hand a Rem ticket to this organization"
    ),
    organization: str | None = _ORG,
    request: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Assign an organization's ticket to a worker, or hand a Rem ticket to an organization."""
    identity = backend.request_id(request)

    def body(client: ApiClient, out: Output) -> CommandResult:
        payload: dict[str, Any] = {
            "action": "assign",
            **_address(client, ticket, organization),
            "assignee_id": assignee or "",
        }
        if to_organization:
            payload["to_organization_id"] = to_organization
        return _act(
            client,
            payload,
            "Assigned.",
            request_id=identity,
            expected_revision=expected_revision,
        )

    backend.run(
        "tickets assign", output, (endpoint, token_file, data_dir), body, request_id=identity
    )


@app.command("comment")
def comment(
    ticket: str = typer.Argument(...),
    text: str = typer.Argument(None, help="The comment, or - for stdin"),
    file: Path | None = typer.Option(None, "--file"),
    organization: str | None = _ORG,
    request: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Add a comment to a ticket's timeline."""
    identity = backend.request_id(request)

    def body(client: ApiClient, out: Output) -> CommandResult:
        payload = {
            "action": "comment",
            **_address(client, ticket, organization),
            "body": backend.read_body(text, file, "comment"),
        }
        return _act(client, payload, "Commented.", request_id=identity)

    backend.run(
        "tickets comment", output, (endpoint, token_file, data_dir), body, request_id=identity
    )


@app.command("comments")
def comments(
    ticket: str = typer.Argument(...),
    organization: str | None = _ORG,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List a ticket's comments and status changes, oldest first."""

    def body(client: ApiClient, out: Output) -> CommandResult:
        timeline = client.post("/rem/kanban/timeline", _address(client, ticket, organization))
        data = {"comments": timeline.get("comments") or [], "earlier": timeline.get("earlier", 0)}
        text = "\n".join(map(_comment_line, data["comments"])) or "No comments."
        return CommandResult(data=data, text=text)

    backend.run("tickets comments", output, (endpoint, token_file, data_dir), body)


def _decide(
    ticket: str,
    expected_revision: int,
    action: str,
    reason: str,
    output: opts.OutputFormat,
    connection: tuple[str | None, Path | None, Path | None],
) -> None:
    def body(client: ApiClient, out: Output) -> CommandResult:
        backend.require_user(f"{action} Rem's proposals")
        payload: dict[str, Any] = {"action": action, **_address(client, ticket, None)}
        if reason:
            payload["reason"] = reason
        done = "Approved." if action == "approve" else "Declined."
        return _act(client, payload, done, expected_revision=expected_revision)

    backend.run(f"tickets {action}", output, connection, body)


@app.command("approve")
def approve(
    ticket: str = typer.Argument(...),
    expected_revision: int = _REVISION,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Approve a Rem proposal, which moves it to To do."""
    connection = (endpoint, token_file, data_dir)
    _decide(ticket, expected_revision, "approve", "", output, connection)


@app.command("decline")
def decline(
    ticket: str = typer.Argument(...),
    expected_revision: int = _REVISION,
    reason: str = typer.Option("", "--reason", help="Why, so Rem doesn't propose it again"),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Decline a Rem proposal, which cancels it."""
    connection = (endpoint, token_file, data_dir)
    _decide(ticket, expected_revision, "decline", reason, output, connection)


@app.command("projects")
def projects(
    paths: list[Path] = typer.Argument(..., help="Open project folders"),
    expected_revision: int = typer.Option(
        ..., "--expected-revision", help="The participant's revision from `tickets list`"
    ),
    participant: str = typer.Option(
        "rem", "--participant", help="rem, or agent:<org>:<worker> from `tickets list`"
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Set the projects a participant may take tickets from in self-work."""

    def body(client: ApiClient, out: Output) -> CommandResult:
        backend.require_user("choose which projects Rem and workers may work in")
        payload = {
            "participant_id": participant,
            "projects": [str(p.expanduser().resolve()) for p in paths],
        }
        board = client.post("/rem/kanban/participant", payload, expected_revision=expected_revision)
        return CommandResult(data=board, text="Saved.")

    backend.run("tickets projects", output, (endpoint, token_file, data_dir), body)


# Self-work ------------------------------------------------------------------------


def _self_work(client: ApiClient) -> dict[str, Any]:
    return dict(client.get("/rem/kanban")["self_work"])


def _status(view: dict[str, Any]) -> CommandResult:
    state, settings = view["state"], view["settings"]
    text = (
        f"Self-work is {state['mode']}: {state.get('detail', '')}\n"
        f"{state['active_threads']}/{state['thread_limit']} threads, "
        f"{state.get('paused_threads', 0)} paused, "
        f"{state['todo_remaining']} to do, {state['proposals_waiting']} proposals waiting. "
        f"Settings r{settings['revision']}: enabled={settings['enabled']}, "
        f"max_threads={settings['max_threads']}, auto_pull_backlog={settings['auto_pull_backlog']}"
    )
    schedule = view.get("schedule") or {}
    if "saved_enabled" in settings and settings["saved_enabled"] != settings["enabled"]:
        text += f" (switch saved as {'on' if settings['saved_enabled'] else 'off'})"
    if schedule.get("active"):
        window = schedule["active"]
        text += f"\nWindow {window['id']}: {window['label']}, {window['after_label']}."
    for window in schedule.get("upcoming") or []:
        text += f"\nNext window {window['id']}: {window['label']}, {window['after_label']}."
    if schedule.get("last_period"):
        last = schedule["last_period"]
        text += f"\nLast period {last['started_at']} to {last['ended_at']}: {last['summary']}"
    return CommandResult(data=view, text=text)


@selfwork_app.command("status")
def selfwork_status(
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show whether self-work is on, what it's doing and its settings."""
    backend.run(
        "selfwork status",
        output,
        (endpoint, token_file, data_dir),
        lambda client, out: _status(_self_work(client)),
    )


@selfwork_app.command("report")
def selfwork_report(
    mark_seen: bool = typer.Option(False, "--mark-seen", help="Mark it read on every device"),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show the running report: tickets added and work done since you last looked."""

    def body(client: ApiClient, out: Output) -> CommandResult:
        view = _self_work(client)
        if mark_seen:
            updated = (view.get("report") or {}).get("updated_at")
            view = dict(
                client.post("/rem/kanban/report/seen", {"updated_at": updated})["self_work"]
            )
        return CommandResult(data=view.get("report") or {})

    backend.run("selfwork report", output, (endpoint, token_file, data_dir), body)


def _put_settings(client: ApiClient, wanted: dict[str, Any], revision: int) -> CommandResult:
    view = client.put("/rem/kanban/settings", wanted, expected_revision=revision)["self_work"]
    return _status(view)


@selfwork_app.command("set")
def selfwork_set(
    expected_revision: int = typer.Option(
        ..., "--expected-revision", help="The settings revision from `selfwork status`"
    ),
    enabled: bool | None = typer.Option(None, "--enabled/--disabled"),
    max_threads: int | None = typer.Option(None, "--max-threads", min=1),
    auto_pull: bool | None = typer.Option(None, "--auto-pull-backlog/--no-auto-pull-backlog"),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Change self-work settings. Self-work spends provider allowance while nobody watches."""

    def body(client: ApiClient, out: Output) -> CommandResult:
        backend.require_user("change Rem's self-work settings")
        wanted: dict[str, Any] = {}
        if enabled is not None:
            wanted["enabled"] = enabled
        if max_threads is not None:
            wanted["max_threads"] = max_threads
        if auto_pull is not None:
            wanted["auto_pull_backlog"] = auto_pull
        if not wanted:
            raise backend.invalid("Change at least one setting.")
        return _put_settings(client, wanted, expected_revision)

    backend.run("selfwork set", output, (endpoint, token_file, data_dir), body)


def _switch(enabled: bool, connection: tuple[Any, Any, Any], output: opts.Output) -> None:
    def body(client: ApiClient, out: Output) -> CommandResult:
        backend.require_user("change Rem's self-work settings")
        revision = int(_self_work(client)["settings"]["revision"])
        return _put_settings(client, {"enabled": enabled}, revision)

    backend.run(f"selfwork {'on' if enabled else 'off'}", output, connection, body)


@selfwork_app.command("on")
def selfwork_on(
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Turn self-work on, as telling Rem "turn on self-work" does. Other settings stay."""
    _switch(True, (endpoint, token_file, data_dir), output)


@selfwork_app.command("off")
def selfwork_off(
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Turn self-work off. Work it already started keeps going; pause or stop it by run."""
    _switch(False, (endpoint, token_file, data_dir), output)


def _clock(value: str | None, when: str | None, timezone: str | None) -> dict[str, Any] | None:
    """A time for a window: HH:MM with an optional date or today/tonight/tomorrow."""
    if not value:
        return None
    moment: dict[str, Any] = {"time": value}
    if when in {"today", "tonight", "tomorrow"}:
        moment["day"] = when
    elif when:
        moment["date"] = when
    if timezone:
        moment["timezone"] = timezone
    return moment


@selfwork_app.command("window")
def selfwork_window(
    until: str | None = typer.Option(None, "--until", help="End time, 24-hour HH:MM"),
    until_day: str | None = typer.Option(
        None, "--until-day", help="today, tonight, tomorrow or YYYY-MM-DD for --until"
    ),
    start: str | None = typer.Option(None, "--from", help="Start time, HH:MM; now if left out"),
    start_day: str | None = typer.Option(
        None, "--from-day", help="today, tonight, tomorrow or YYYY-MM-DD for --from"
    ),
    timezone: str | None = typer.Option(
        None, "--timezone", help="IANA time zone, such as Asia/Irkutsk; this computer's if left out"
    ),
    running_done: bool = typer.Option(
        False, "--until-running-done", help="End when your tasks running now finish"
    ),
    then: str = typer.Option(
        "restore", "--then", help="The switch after the window: restore, on or off"
    ),
    wait_for_idle: bool = typer.Option(
        False, "--wait-for-idle", help="Inside the window, still wait for 15 quiet minutes"
    ),
    stop: bool = typer.Option(
        False, "--stop", help="End the self-work running now at --until, then leave it off"
    ),
    cancel: str | None = typer.Option(None, "--cancel", help="Cancel or end this window ID"),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Run self-work for a stretch of time, end it at a time, or cancel a window."""

    def body(client: ApiClient, out: Output) -> CommandResult:
        backend.require_user("change Rem's self-work")
        payload: dict[str, Any]
        if cancel:
            payload = {"action": "cancel", "window_id": cancel}
        elif stop:
            if not until or start or running_done:
                raise backend.invalid("--stop takes --until only.")
            payload = {"action": "stop_at", "end": _clock(until, until_day, timezone)}
        else:
            if bool(until) == running_done:
                raise backend.invalid("Give --until or --until-running-done.")
            if then not in {"restore", "on", "off"}:
                raise backend.invalid("--then is restore, on or off.")
            payload = {
                "action": "schedule",
                "after_end": then,
                "wait_for_idle": wait_for_idle,
            }
            if running_done:
                payload["until"] = "running_done"
            else:
                payload["end"] = _clock(until, until_day, timezone)
            if start:
                payload["start"] = _clock(start, start_day, timezone)
        view = client.post("/rem/kanban/window", payload)["self_work"]
        return _status(view)

    backend.run("selfwork window", output, (endpoint, token_file, data_dir), body)


@selfwork_app.command("export")
def selfwork_export(
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Print the self-work settings as a JSON file for `selfwork apply`."""

    def body(client: ApiClient, out: Output) -> None:
        settings = _self_work(client)["settings"]
        wanted = {key: settings[key] for key in SELF_WORK_SETTINGS}
        # The switch as saved, not as a timed window shows it right now.
        wanted["enabled"] = settings.get("saved_enabled", settings["enabled"])
        out.stdout.write(json.dumps(wanted, indent=2) + "\n")
        out.stdout.flush()

    backend.run("selfwork export", opts.OutputFormat.json, (endpoint, token_file, data_dir), body)


@selfwork_app.command("apply")
def selfwork_apply(
    file: Path = typer.Option(..., "--file", "-f", help="JSON from `selfwork export`, or -"),
    expected_revision: int | None = typer.Option(
        None, "--expected-revision", help="Refuse if the settings changed since this revision"
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Apply self-work settings from a file. Without --expected-revision it replaces them."""

    def body(client: ApiClient, out: Output) -> CommandResult:
        backend.require_user("change Rem's self-work settings")
        wanted = backend.read_json_file(file)
        if not isinstance(wanted, dict) or not wanted or set(wanted) - set(SELF_WORK_SETTINGS):
            raise backend.invalid("The file holds enabled, max_threads and auto_pull_backlog only.")
        revision = expected_revision
        if revision is None:
            revision = int(_self_work(client)["settings"]["revision"])
        return _put_settings(client, wanted, revision)

    backend.run("selfwork apply", output, (endpoint, token_file, data_dir), body)
