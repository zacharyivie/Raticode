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
SELF_WORK_SETTINGS = (
    "enabled",
    "max_threads",
    "auto_pull_backlog",
    "session_token_limit",
    "daily_token_limit",
    "rate_limit_percent",
    "daily_allowance_percent",
    "allowance_mode",
    "pace_aim_percent",
    "pace_lead_hours",
    "auto_approve_proposals",
    "provider_accounts",
)
# Settings the status line shows as key=value; accounts get a line of their own.
_SHOWN_SETTINGS = tuple(key for key in SELF_WORK_SETTINGS[3:] if key != "provider_accounts")
# Off, Daily cap or Pace to reset (rem_self_work_pace.MODES, kept here so the CLI starts fast).
ALLOWANCE_MODES = ("off", "daily", "pace")
AUTO_APPROVE_WARNING = (
    "With auto-approval on, ideas Rem's self-work research finds go to Backlog already "
    "approved, for as long as self-work runs, without you reading them first; with backlog "
    "pulling on, Rem then works on them. Proposals already waiting still need you. Add "
    "--confirm-auto-approve to turn it on."
)
_ORG = typer.Option(None, "--org", help="Organization ID, for an organization's ticket")
_REVISION = typer.Option(..., "--expected-revision", help="The ticket's revision from `show`")
_WAITS_HELP = (
    "A ticket this one waits for, by key such as REM-3 or by ID; repeat for several. "
    "Self-work skips the ticket until all of them are Done."
)


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
    if ticket.get("proposed"):
        on = ticket.get("proposed_on")
        flags = f" (proposal from {on})" if on else " (proposal)"
    else:
        flags = " (running)" if ticket.get("running") else ""
    waits = [w for w in ticket.get("waits_for") or [] if w.get("status") != "done"]
    if waits:
        flags += " (waits for " + ", ".join(w.get("key") or w["title"] for w in waits) + ")"
    if ticket.get("violates_decisions"):
        flags += " (violates " + ", ".join(ticket["violates_decisions"]) + ")"
    flags += "".join(f" [{tag}]" for tag in ticket.get("tags") or [])
    return (
        f"{ticket.get('key') or ticket['ticket_id']:<10} {ticket['status']:<11} "
        f"r{ticket['revision']:<3} {where}: {ticket['title']}{flags}"
    )


def _waits_lines(ticket: dict[str, Any]) -> list[str]:
    """What a ticket waits for, and the notice when one of them was cancelled."""
    waits = ticket.get("waits_for") or []
    if not waits:
        return []
    lines = [
        "Waits for: "
        + "; ".join(f"{w.get('key') or w['ticket_id']} {w['title']} ({w['status']})" for w in waits)
    ]
    if ticket.get("wait_notice"):
        lines.append(f"Notice: {ticket['wait_notice']}")
    return lines


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

    from gofer.core.usage_ledger import usage_label

    def body(client: ApiClient, out: Output) -> CommandResult:
        timeline = client.post("/rem/kanban/timeline", _address(client, ticket, organization))
        lines = [
            _line(timeline["ticket"]),
            f"Usage: {usage_label(timeline['ticket'].get('usage'))}",
            *_waits_lines(timeline["ticket"]),
            *(
                [timeline["ticket"]["violation_reason"]]
                if timeline["ticket"].get("violation_reason")
                else []
            ),
            "",
            timeline["ticket"].get("description") or "",
        ]
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
    waits_for: list[str] | None = typer.Option(None, "--waits-for", help=_WAITS_HELP),
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
        if waits_for:
            payload["waits_for"] = list(waits_for)
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
    waits_for: list[str] | None = typer.Option(
        None, "--waits-for", help=_WAITS_HELP + " Replaces the whole list."
    ),
    clear_waits: bool = typer.Option(False, "--clear-waits", help="Stop waiting for other tickets"),
    organization: str | None = _ORG,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Change a ticket's title, description, priority, type, project, finish line or waits."""

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
        if waits_for and clear_waits:
            raise backend.invalid("Use --waits-for or --clear-waits, not both.")
        if waits_for or clear_waits:
            changes["waits_for"] = list(waits_for or [])
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
        + "".join(f", {key}={settings[key]}" for key in _SHOWN_SETTINGS if key in settings)
    )
    accounts = _accounts_line(view)
    if accounts:
        text += f"\n{accounts}"
    schedule = view.get("schedule") or {}
    if "saved_enabled" in settings and settings["saved_enabled"] != settings["enabled"]:
        text += f" (switch saved as {'on' if settings['saved_enabled'] else 'off'})"
    if settings.get("auto_approve_proposals"):
        text += (
            f"\nAuto-approval is on: {state.get('auto_approved_session', 0)} "
            "tickets approved by self-work this session."
        )
    today = state.get("daily_allowance") or {}
    if today.get("message"):
        text += f"\n{today['message']}"
    paced = state.get("allowance_pace") or {}
    if paced.get("message"):
        text += f"\n{paced['message']}"
    if schedule.get("active"):
        window = schedule["active"]
        text += f"\nWindow {window['id']}: {window['label']}, {window['after_label']}."
    for window in schedule.get("upcoming") or []:
        text += f"\nNext window {window['id']}: {window['label']}, {window['after_label']}."
    if schedule.get("last_period"):
        last = schedule["last_period"]
        text += f"\nLast period {last['started_at']} to {last['ended_at']}: {last['summary']}"
    stop_text = _stop_text(view.get("stop") or {})
    if stop_text:
        text += f"\n{stop_text}"
    return CommandResult(data=view, text=text)


def _stop_text(stop: dict[str, Any]) -> str:
    """When self-work stops and the last check, or the last stop after a period ended."""
    if stop.get("active"):
        text = f"{stop.get('label')} (stop r{stop.get('revision', 0)})."
        judgment = stop.get("judgment")
        if stop.get("judging"):
            text += " Checking the condition now."
        elif judgment:
            verdict = "met" if judgment.get("met") else "not met"
            text += f" Last check {judgment.get('at_label')}: {verdict}. {judgment.get('evidence')}"
        elif stop.get("condition"):
            text += " Not checked yet."
        if stop.get("judge_failed"):
            text += " The last check failed; it's tried again in 10 minutes."
        return text.strip()
    last = stop.get("last") or {}
    if last.get("condition"):
        judgment = last.get("judgment") or {}
        verdict = "met" if judgment.get("met") else "not met" if judgment else "never checked"
        return f"Last stop condition “{last['condition']}”: {verdict}."
    return ""


def _choice_label(choice: str, rem_setting: str) -> str:
    if choice == "@default":
        return "default sign-in"
    if choice == "auto":
        return "choose by usage"
    if choice:
        return choice
    rem = "choose by usage" if rem_setting == "auto" else rem_setting or "default sign-in"
    return f"Rem's setting ({rem})"


def _accounts_line(view: dict[str, Any]) -> str:
    """ "Self-work accounts: Claude Code: work; Codex: Rem's setting (default sign-in)"."""
    rows = view.get("accounts") or []
    if not rows:
        return ""
    parts = [
        f"{row['label']}: {_choice_label(row.get('choice') or '', row.get('rem_setting') or '')}"
        for row in rows
    ]
    return "Self-work accounts: " + "; ".join(parts)


def _account_choices(values: list[str]) -> dict[str, str]:
    """PROVIDER=ACCOUNT pairs from --account; an empty ACCOUNT means Rem's setting."""
    choices: dict[str, str] = {}
    for value in values:
        provider, sep, choice = value.partition("=")
        if not sep or not provider.strip():
            raise backend.invalid(
                f"--account takes PROVIDER=ACCOUNT, such as claude_code=work, not {value!r}."
            )
        choices[provider.strip()] = choice.strip()
    return choices


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
    session_tokens: int | None = typer.Option(
        None,
        "--session-token-limit",
        min=0,
        help="Start no new work past this many tokens in one self-work session; 0 is no limit",
    ),
    daily_tokens: int | None = typer.Option(
        None,
        "--daily-token-limit",
        min=0,
        help="Start no new work past this many self-work tokens in a day; 0 is no limit",
    ),
    rate_percent: int | None = typer.Option(
        None,
        "--rate-limit-percent",
        min=0,
        max=100,
        help="Start no new work while a stored rate-limit window of the account self-work "
        "would run on is at or above this percent; 0 is no limit",
    ),
    daily_allowance: int | None = typer.Option(
        None,
        "--daily-allowance-percent",
        min=0,
        max=100,
        help="Start no new work once self-work used this percent of the weekly allowance of "
        "the account it runs on today (resets at local midnight); 0 is no cap. A percent "
        "above 0 chooses Daily cap",
    ),
    allowance_mode: str | None = typer.Option(
        None,
        "--allowance-mode",
        help="How self-work uses a weekly allowance: off, daily (Daily cap, with "
        "--daily-allowance-percent) or pace (Pace to reset: start new work only while the "
        "account is under a line that reaches the aim shortly before its reset)",
    ),
    pace_aim: int | None = typer.Option(
        None,
        "--pace-aim",
        min=1,
        max=100,
        help="Pace to reset: the percent of the allowance to reach by the reset, from 1 to 100 "
        "(default 97)",
    ),
    pace_lead: int | None = typer.Option(
        None,
        "--pace-lead",
        min=0,
        max=24,
        help="Pace to reset: reach the aim this many hours before the reset (default 3)",
    ),
    auto_approve: bool | None = typer.Option(
        None,
        "--auto-approve/--no-auto-approve",
        help="Let self-work approve the backlog tickets its research finds, until self-work "
        "ends, instead of waiting for you; turning it on needs --confirm-auto-approve",
    ),
    confirm_auto_approve: bool = typer.Option(
        False,
        "--confirm-auto-approve",
        help="Confirm you want self-work to approve its own tickets without asking you",
    ),
    account: list[str] | None = typer.Option(
        None,
        "--account",
        help="PROVIDER=ACCOUNT: the account runs self-work starts use with that provider. "
        "ACCOUNT is an account name, auto (choose by usage), @default (the default sign-in) "
        "or empty for Rem's setting (Settings > Rem > Accounts); repeat for several providers",
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Change self-work settings. Self-work spends provider allowance while nobody watches."""

    def body(client: ApiClient, out: Output) -> CommandResult:
        backend.require_user("change Rem's self-work settings")
        wanted: dict[str, Any] = {}
        if auto_approve is not None:
            if auto_approve and not confirm_auto_approve:
                raise backend.invalid(AUTO_APPROVE_WARNING)
            wanted["auto_approve_proposals"] = auto_approve
            if auto_approve:
                wanted["confirm_auto_approve"] = True
        if enabled is not None:
            wanted["enabled"] = enabled
        if max_threads is not None:
            wanted["max_threads"] = max_threads
        if auto_pull is not None:
            wanted["auto_pull_backlog"] = auto_pull
        for key, value in (
            ("session_token_limit", session_tokens),
            ("daily_token_limit", daily_tokens),
            ("rate_limit_percent", rate_percent),
            ("daily_allowance_percent", daily_allowance),
            ("pace_aim_percent", pace_aim),
            ("pace_lead_hours", pace_lead),
        ):
            if value is not None:
                wanted[key] = value
        if allowance_mode is not None:
            if allowance_mode not in ALLOWANCE_MODES:
                raise backend.invalid("--allowance-mode takes off, daily or pace.")
            wanted["allowance_mode"] = allowance_mode
        if account:
            wanted["provider_accounts"] = _account_choices(account)
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


@selfwork_app.command("start")
def selfwork_start(
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Start self-work now, without waiting for 15 quiet minutes; turns it on if it is off.

    Self-work keeps starting work until it is turned off, within its thread limit, objectives,
    allowance, projects and stop condition. Telling Rem "start self-work now" does the same.
    """

    def body(client: ApiClient, out: Output) -> CommandResult:
        backend.require_user("start Rem's self-work")
        revision = int(_self_work(client)["settings"]["revision"])
        return _put_settings(client, {"start_now": True}, revision)

    backend.run("selfwork start", output, (endpoint, token_file, data_dir), body)


@selfwork_app.command("on")
def selfwork_on(
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Turn self-work on, as telling Rem "turn on self-work" does. Other settings stay.

    It starts after 15 minutes without a prompt; `selfwork start` starts it now.
    """
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


@selfwork_app.command("stop-when")
def selfwork_stop_when(
    condition: str | None = typer.Argument(
        None, help="Stop when this is true, in your words, e.g. 'REM-205 to REM-212 are done'"
    ),
    no_later_than: str | None = typer.Option(
        None, "--no-later-than", help="Stop by this time whatever the condition says, HH:MM"
    ),
    day: str | None = typer.Option(
        None, "--day", help="today, tonight, tomorrow or YYYY-MM-DD for --no-later-than"
    ),
    timezone: str | None = typer.Option(
        None, "--timezone", help="IANA time zone, such as Asia/Irkutsk; this computer's if left out"
    ),
    clear: str | None = typer.Option(
        None, "--clear", help="Clear condition, no-later-than or both"
    ),
    revision: int | None = typer.Option(
        None, "--revision", help="Refuse if the stop changed since this revision"
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Stop the self-work running now when a condition is met, or by a time at the latest."""

    def body(client: ApiClient, out: Output) -> CommandResult:
        backend.require_user("change when Rem's self-work stops")
        payload: dict[str, Any] = {}
        if clear is not None:
            names = {"condition": "condition", "no-later-than": "no_later_than", "both": "both"}
            if clear not in names:
                raise backend.invalid("--clear is condition, no-later-than or both.")
            payload["clear"] = names[clear]
        if condition is not None:
            payload["condition"] = condition
        if no_later_than:
            payload["no_later_than"] = _clock(no_later_than, day, timezone)
        elif day or timezone:
            raise backend.invalid("--day and --timezone go with --no-later-than.")
        if not payload:
            raise backend.invalid("Give a condition, --no-later-than, or --clear.")
        if revision is not None:
            payload["expected_revision"] = revision
        view = client.post("/rem/kanban/stop", payload)["self_work"]
        return _status(view)

    backend.run("selfwork stop-when", output, (endpoint, token_file, data_dir), body)


OBJECTIVE_ACTIONS = ("list", "add", "edit", "remove", "check", "uncheck")


def _objective_line(item: dict[str, Any]) -> str:
    mark = "x" if item.get("done") else " "
    line = f"[{mark}] {item['text']}  ({item['id']})"
    if item.get("done_when"):
        line += f"\n      done when: {item['done_when']}"
    if item.get("done") and item.get("done_evidence"):
        line += f"\n      met: {item['done_evidence']}"
    return line


def _objectives_text(view: dict[str, Any]) -> str:
    """Current objectives (open above done, newest first), then the stale ones."""
    items = view.get("items") or []
    if not view.get("total") and not items:
        return "No self-work objectives. Rem writes its own when self-work starts with none."
    done, total = (
        int(view.get("done") or 0),
        int(view.get("open") or 0) + int(view.get("done") or 0),
    )
    lines = [f"Objectives (revision {view.get('revision', 0)}), {done}/{total} done:"]
    current = [i for i in items if not (i.get("done") and i.get("stale"))]
    stale = [i for i in items if i.get("done") and i.get("stale")]
    lines += [_objective_line(i) for i in current]
    if stale:
        lines.append("Stale (done in earlier self-work sessions):")
        lines += [_objective_line(i) for i in stale]
    shown_to = int(view.get("offset") or 0) + len(items)
    if shown_to < int(view.get("total") or 0):
        lines.append(f"{int(view['total']) - shown_to} more; list again with --offset {shown_to}.")
    return "\n".join(lines)


@selfwork_app.command("objectives")
def selfwork_objectives(
    action: str = typer.Argument("list", help="list, add, edit, remove, check or uncheck"),
    text: str | None = typer.Option(None, "--text", help="The objective, for add or edit"),
    done_when: str | None = typer.Option(
        None, "--done-when", help="Its doneness criteria; leave out for an open-ended one"
    ),
    identity: str | None = typer.Option(None, "--id", help="The objective's ID from `list`"),
    evidence: str | None = typer.Option(None, "--evidence", help="How it was met, for check"),
    limit: int = typer.Option(30, "--limit", min=1, max=500, help="How many to list"),
    offset: int = typer.Option(0, "--offset", min=0, help="How many to skip, for list"),
    revision: int | None = typer.Option(
        None, "--expected-revision", help="The list's revision from `list`"
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show or change the self-work objectives: the goals that guide a whole session.

    They list newest first, open above done; ones done in an earlier session are stale.
    """

    def body(client: ApiClient, out: Output) -> CommandResult:
        if action not in OBJECTIVE_ACTIONS:
            raise backend.invalid(f"Choose one of: {', '.join(OBJECTIVE_ACTIONS)}.")
        if action == "list":
            view = client.get("/rem/objectives", query={"limit": limit, "offset": offset})
            return CommandResult(data=view, text=_objectives_text(view))
        backend.require_user("change Rem's self-work objectives")
        if action == "add" and not text:
            raise backend.invalid("add needs --text.")
        if action != "add" and not identity:
            raise backend.invalid(f"{action} needs --id.")
        payload: dict[str, Any] = {"action": action, "limit": limit}
        for key, value in (
            ("text", text),
            ("done_when", done_when),
            ("id", identity),
            ("evidence", evidence),
            ("expected_revision", revision),
        ):
            if value is not None:
                payload[key] = value
        view = client.post("/rem/objectives", payload)
        return CommandResult(data=view, text=_objectives_text(view))

    backend.run("selfwork objectives", output, (endpoint, token_file, data_dir), body)


@selfwork_app.command("export")
def selfwork_export(
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Print the self-work settings as a JSON file for `selfwork apply`."""

    def body(client: ApiClient, out: Output) -> None:
        settings = _self_work(client)["settings"]
        # An older backend has no limit settings; export what it has.
        wanted = {key: settings[key] for key in SELF_WORK_SETTINGS if key in settings}
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
    confirm_auto_approve: bool = typer.Option(
        False,
        "--confirm-auto-approve",
        help="Needed when the file turns auto-approval on: self-work then approves its own "
        "backlog tickets without asking you",
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
            raise backend.invalid(
                "The file holds only these settings: " + ", ".join(SELF_WORK_SETTINGS)
            )
        current = _self_work(client)["settings"]
        if wanted.get("auto_approve_proposals") is True and not current.get(
            "auto_approve_proposals"
        ):
            if not confirm_auto_approve:
                raise backend.invalid(AUTO_APPROVE_WARNING)
            wanted["confirm_auto_approve"] = True
        accounts = wanted.get("provider_accounts")
        if isinstance(accounts, dict):
            # The file replaces every choice: a provider it leaves out follows Rem's setting.
            saved = current.get("provider_accounts") or {}
            wanted["provider_accounts"] = {**{p: "" for p in saved}, **accounts}
        revision = expected_revision
        if revision is None:
            revision = int(current["revision"])
        return _put_settings(client, wanted, revision)

    backend.run("selfwork apply", output, (endpoint, token_file, data_dir), body)
