"""Organizations through a running Raticode backend.

Read, configure, employees, work schedule, tickets, projects and knowledge. Starting,
pausing or resuming an organization and deciding consent requests stay in the
desktop's Organizations tab: they are not CLI commands.

Organizations are a Feature Preview. The backend serves them only when it runs
with RATICODE_EXPERIMENTAL_ORGANIZATIONS=1; otherwise these commands exit 5.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import typer

from gofer.cli import client_options as opts
from gofer.cli.client import ExitCode, Output
from gofer.cli.commands import _resources as res
from gofer.cli.commands._backend import require_user
from gofer.cli.commands._resources import Backend, failure, finish, session, table

app = typer.Typer(
    help=(
        "Organizations through the running backend: read, configure, employees, work "
        "schedule, tickets, projects and knowledge. Start, pause and consent decisions "
        "stay in the desktop app."
    ),
    no_args_is_help=True,
)
employees_app = typer.Typer(help="List workers and hold or release one.", no_args_is_help=True)
schedule_app = typer.Typer(help="Read or replace the work schedule.", no_args_is_help=True)
tickets_app = typer.Typer(help="Organization tickets (tasks).", no_args_is_help=True)
knowledge_app = typer.Typer(help="Shared organization knowledge.", no_args_is_help=True)
app.add_typer(employees_app, name="employees")
app.add_typer(schedule_app, name="schedule")
app.add_typer(tickets_app, name="tickets")
app.add_typer(knowledge_app, name="knowledge")

# The same set the backend uses to decide which workers need a desktop review.
_UNSANDBOXED = {"danger-full-access", "bypassPermissions"}


def call(backend: Backend, action: str, organization_id: str | None = None, **params: Any) -> Any:
    body: dict[str, Any] = {"action": action, "params": params}
    if organization_id:
        body["organizationId"] = organization_id
    return backend.request("POST", "/organizations", body).get("result")


def _read(backend: Backend, organization_id: str) -> dict[str, Any]:
    return dict(call(backend, "read", organization_id))


@app.command("list")
def list_organizations(
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List organizations with their state and open tickets."""
    current = session("org list", output, endpoint, token_file, data_dir)

    def text(data: list[dict[str, Any]]) -> str:
        rows = [
            [
                str(o["id"]),
                str(o.get("name")),
                str(o.get("state")) + (" (archived)" if o.get("archived") else ""),
                str(o.get("employees")),
                str(o.get("openTasks")),
            ]
            for o in data
        ]
        return table(rows, ["ID", "NAME", "STATE", "WORKERS", "OPEN"]) or "No organizations."

    finish(current, lambda b, _o: call(b, "list"), text)


@app.command("show")
def show_organization(
    organization_id: str = typer.Argument(...),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show an organization's configuration, state and runtime."""
    current = session("org show", output, endpoint, token_file, data_dir)

    def text(data: dict[str, Any]) -> str:
        config = data.get("config") or {}
        runtime = data.get("runtime") or {}
        lines = [
            f"{config.get('name')} ({organization_id}), revision {data.get('revision')}",
            f"State:    {runtime.get('state')}"
            + (f" ({runtime['pauseReason']})" if runtime.get("pauseReason") else ""),
            f"Workers:  {len(config.get('employees') or [])}",
            f"Projects: {', '.join(config.get('projectRoots') or []) or 'none'}",
            f"Tickets:  {len(runtime.get('tasks') or [])}",
        ]
        if config.get("mission"):
            lines.append(f"Mission:  {config['mission']}")
        return "\n".join(lines)

    finish(current, lambda b, _o: _read(b, organization_id), text)


@app.command("history")
def organization_history(
    organization_id: str = typer.Argument(...),
    limit: int = typer.Option(20, "--limit"),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Configuration revisions, newest first."""
    current = session("org history", output, endpoint, token_file, data_dir)

    def text(data: list[dict[str, Any]]) -> str:
        rows = [
            [
                str(e.get("revision")),
                str(e.get("createdAt") or e.get("at") or ""),
                str(e.get("actor") or ""),
                str(e.get("reason") or ""),
            ]
            for e in data
        ]
        return table(rows, ["REV", "AT", "BY", "REASON"]) or "No history."

    finish(current, lambda b, _o: call(b, "history", organization_id, limit=limit), text)


@app.command("events")
def organization_events(
    organization_id: str = typer.Argument(...),
    after: int | None = typer.Option(None, "--after", help="Only entries after this sequence."),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Activity entries. Text output is one JSON entry per line."""
    current = session("org events", output, endpoint, token_file, data_dir)
    params = {} if after is None else {"after": after}

    def text(data: Any) -> str:
        entries = data.get("events", data) if isinstance(data, dict) else data
        return "\n".join(json.dumps(entry) for entry in entries or [])

    finish(current, lambda b, _o: call(b, "events", organization_id, **params), text)


# Config as files -------------------------------------------------------------------


@app.command("config")
def organization_config(
    organization_id: str = typer.Argument(...),
    file: Path | None = typer.Option(
        None, "--file", "-f", help="File to write; default the envelope's data."
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Write an organization's configuration file for editing and `org apply`.

    The file records the revision it came from, so `org apply` refuses it (exit 4) if
    someone changed the organization in between.
    """
    current = session("org config", output, endpoint, token_file, data_dir)

    def fetch(backend: Backend, out: Output) -> dict[str, Any]:
        org = _read(backend, organization_id)
        document = {
            "organizationId": organization_id,
            "revision": org.get("revision"),
            "config": org.get("config"),
        }
        if file is not None:
            res.write_json_file(file, document)
        return document

    finish(
        current,
        fetch,
        lambda d: f"Wrote revision {d['revision']} to {file}" if file else json.dumps(d, indent=2),
    )


@app.command("apply")
def apply_organization(
    file: Path = typer.Option(..., "--file", "-f", help="File from `org config`; - for stdin."),
    reason: str = typer.Option("Applied from the Raticode CLI", "--reason"),
    expected_revision: int | None = typer.Option(
        None, "--expected-revision", help="Overrides the revision recorded in the file."
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Replace an organization's configuration with a file, or create one.

    A file with an organizationId updates that organization; one without creates a new,
    paused organization. Configuration never starts an organization. New project
    folders must be ones the backend already trusts.
    """
    current = session("org apply", output, endpoint, token_file, data_dir)
    document = res.read_json_file(current, file)
    if not isinstance(document, dict) or not isinstance(document.get("config"), dict):
        res.invalid(current, "The file needs a config object (see `org config`)")
    organization_id = document.get("organizationId")
    revision = expected_revision if expected_revision is not None else document.get("revision")
    if organization_id and not isinstance(revision, int):
        res.invalid(current, "The file has no revision; pass --expected-revision")

    def apply(backend: Backend, out: Output) -> Any:
        # A configuration can grant workers full access or new folders.
        require_user("change an organization's configuration")
        if not organization_id:
            result = call(backend, "create", None, config=document["config"], reason=reason)
        else:
            result = call(
                backend,
                "configure",
                str(organization_id),
                config=document["config"],
                expectedRevision=revision,
                reason=reason,
            )
        if isinstance(result, dict) and result.get("needsUserConsent"):
            raise failure(
                "The backend recorded a consent request; approve it in the desktop app.",
                ExitCode.NEEDS_USER,
                data=result,
            )
        return result

    def text(data: Any) -> str:
        identifier = data.get("id") if isinstance(data, dict) else None
        return f"Applied {identifier or organization_id}."

    finish(current, apply, text)


@app.command("export")
def export_organization(
    organization_id: str = typer.Argument(...),
    file: Path = typer.Option(..., "--file", "-f", help="ZIP file to write."),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Export the portable organization package (Agent Companies format) as a ZIP."""
    current = session("org export", output, endpoint, token_file, data_dir)

    def export(backend: Backend, out: Output) -> dict[str, Any]:
        package = call(backend, "export", organization_id)
        file.write_bytes(base64.b64decode(package["zip"]))
        return {"path": str(file.resolve()), "files": sorted(package.get("files", {}))}

    finish(current, export, lambda d: f"Wrote {len(d['files'])} package files to {d['path']}")


@app.command("import")
def import_organization(
    package: Path = typer.Argument(..., help="Package ZIP from `org export`."),
    preview: bool = typer.Option(False, "--preview", help="Check it without creating anything."),
    reason: str = typer.Option("Imported from the Raticode CLI", "--reason"),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Import an organization package. It arrives paused, with every worker held."""
    current = session("org import", output, endpoint, token_file, data_dir)
    try:
        encoded = base64.b64encode(package.read_bytes()).decode()
    except OSError as exc:
        res.invalid(current, f"Could not read {package}: {exc.strerror or exc}")
    if preview:
        finish(current, lambda b, _o: call(b, "import_preview", zip=encoded))
        return
    finish(
        current,
        lambda b, _o: call(b, "import", zip=encoded, reason=reason),
        lambda d: f"Imported {d.get('id') if isinstance(d, dict) else ''} (paused).",
    )


# Employees -------------------------------------------------------------------------


@employees_app.command("list")
def list_employees(
    organization_id: str = typer.Argument(...),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List workers with their role, provider and whether they're held."""
    current = session("org employees list", output, endpoint, token_file, data_dir)

    def text(data: list[dict[str, Any]]) -> str:
        rows = [
            [
                str(e.get("id")),
                str(e.get("name")),
                str(e.get("role") or e.get("title") or ""),
                str(e.get("provider") or ""),
                "held" if e.get("paused") else "active",
            ]
            for e in data
        ]
        return table(rows, ["ID", "NAME", "ROLE", "PROVIDER", "STATE"]) or "No workers."

    finish(
        current,
        lambda b, _o: list((_read(b, organization_id).get("config") or {}).get("employees") or []),
        text,
    )


def _hold(paused: bool) -> Any:
    def command(
        organization_id: str = typer.Argument(...),
        employee_id: str = typer.Argument(...),
        output: opts.Output = opts.OutputFormat.json,
        endpoint: opts.EndpointUrl = None,
        token_file: opts.TokenFile = None,
        data_dir: opts.DataDir = None,
    ) -> None:
        verb = "hold" if paused else "release"
        current = session(f"org employees {verb}", output, endpoint, token_file, data_dir)

        def apply(backend: Backend, out: Output) -> dict[str, Any]:
            if not paused:
                require_user("release a worker's hold")
            org = _read(backend, organization_id)
            config = dict(org["config"])
            employees = list(config.get("employees") or [])
            found = next((e for e in employees if e.get("id") == employee_id), None)
            if found is None:
                raise failure(
                    f"Worker '{employee_id}' isn't in this organization", ExitCode.NOT_FOUND
                )
            if found.get("paused") is paused:
                return {"id": employee_id, "paused": paused, "changed": False}
            if not paused and found.get("permissionMode") in _UNSANDBOXED:
                # Like the phone: a worker that runs without a sandbox is released
                # from the desktop, where its access can be reviewed.
                raise failure(
                    "This worker runs without a sandbox. Release it from the desktop app, "
                    "where you can review its access.",
                    ExitCode.AUTH,
                    code="desktop_review_required",
                )
            config["employees"] = [
                {**e, "paused": paused} if e.get("id") == employee_id else e for e in employees
            ]
            name = found.get("name") or employee_id
            call(
                backend,
                "configure",
                organization_id,
                config=config,
                expectedRevision=org["revision"],
                reason=f"{'Held' if paused else 'Released'} {name} from the CLI",
            )
            return {"id": employee_id, "paused": paused, "changed": True}

        finish(
            current,
            apply,
            lambda d: (
                f"{employee_id} is {'held' if d['paused'] else 'released'}"
                + ("" if d["changed"] else " (unchanged)")
            ),
        )

    command.__doc__ = (
        "Hold a worker so it takes no new tickets."
        if paused
        else "Release a worker's hold. Workers without a sandbox are released in the app."
    )
    return command


employees_app.command("hold")(_hold(True))
employees_app.command("release")(_hold(False))


# Work schedule ---------------------------------------------------------------------


@schedule_app.command("show")
def show_schedule(
    organization_id: str = typer.Argument(...),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show the work schedule blocks, time zone and the revision to apply against."""
    current = session("org schedule show", output, endpoint, token_file, data_dir)

    def fetch(backend: Backend, out: Output) -> dict[str, Any]:
        org = _read(backend, organization_id)
        config = org.get("config") or {}
        hours = config.get("operatingHours") or {}
        return {
            "organizationId": organization_id,
            "revision": org.get("revision"),
            "timezone": hours.get("timezone", "UTC"),
            "blocks": (config.get("workSchedule") or {}).get("blocks", []),
        }

    def text(data: dict[str, Any]) -> str:
        rows = [
            [
                str(b.get("label") or b.get("id")),
                str(b.get("cadence") or ""),
                f"{b.get('start', '')}-{b.get('end', '')}",
                str(b.get("focus") or ""),
            ]
            for b in data["blocks"]
        ]
        return f"Time zone {data['timezone']}, revision {data['revision']}\n" + (
            table(rows, ["BLOCK", "CADENCE", "HOURS", "FOCUS"]) or "No blocks."
        )

    finish(current, fetch, text)


@schedule_app.command("apply")
def apply_schedule(
    organization_id: str = typer.Argument(...),
    file: Path = typer.Option(
        ..., "--file", "-f", help="JSON from `org schedule show --format json` (its data)."
    ),
    reason: str = typer.Option("Changed the work schedule from the CLI", "--reason"),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Replace the work schedule and its time zone. Stale revisions exit 4."""
    current = session("org schedule apply", output, endpoint, token_file, data_dir)
    document = res.read_json_file(current, file)
    if not isinstance(document, dict) or not isinstance(document.get("blocks"), list):
        res.invalid(current, "The file needs a blocks list")
    if not isinstance(document.get("revision"), int):
        res.invalid(current, "The file needs the revision it was read at")

    def apply(backend: Backend, out: Output) -> Any:
        item: dict[str, Any] = {"workSchedule": {"blocks": document["blocks"]}}
        zone = document.get("timezone")
        if isinstance(zone, str) and zone:
            org = _read(backend, organization_id)
            hours = (org.get("config") or {}).get("operatingHours") or {"mode": "always"}
            if zone != hours.get("timezone", "UTC"):
                item["operatingHours"] = {**hours, "timezone": zone}
        return call(
            backend,
            "plan_save",
            organization_id,
            kind="operations",
            item=item,
            expectedRevision=document["revision"],
            reason=reason,
        )

    finish(current, apply, lambda d: "Saved the work schedule.")


# Projects --------------------------------------------------------------------------


@app.command("projects")
def organization_projects(
    organization_id: str = typer.Argument(...),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List the organization's project folders and its projects (initiatives).

    Change them with `org config` and `org apply`.
    """
    current = session("org projects", output, endpoint, token_file, data_dir)

    def fetch(backend: Backend, out: Output) -> dict[str, Any]:
        config = _read(backend, organization_id).get("config") or {}
        return {
            "projectRoots": config.get("projectRoots") or [],
            "projects": config.get("projects") or [],
        }

    def text(data: dict[str, Any]) -> str:
        rows = [
            [str(p.get("id")), str(p.get("name")), str(p.get("workspacePath") or "")]
            for p in data["projects"]
        ]
        folders = "\n".join(f"Folder: {root}" for root in data["projectRoots"])
        return (
            "\n".join(part for part in (folders, table(rows, ["ID", "NAME", "FOLDER"])) if part)
            or "No projects."
        )

    finish(current, fetch, text)


# Tickets ---------------------------------------------------------------------------


@tickets_app.command("list")
def list_tickets(
    organization_id: str = typer.Argument(...),
    status: str | None = typer.Option(None, "--status", help="backlog, todo, in_progress, ..."),
    assignee: str | None = typer.Option(None, "--assignee", help="Worker ID."),
    project: str | None = typer.Option(None, "--project", help="Project (initiative) ID."),
    query: str | None = typer.Option(None, "--query", help="Words in the title or description."),
    limit: int = typer.Option(50, "--limit"),
    offset: int = typer.Option(0, "--offset"),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List and search tickets."""
    current = session("org tickets list", output, endpoint, token_file, data_dir)
    params = {
        key: value
        for key, value in {
            "status": status,
            "assignee": assignee,
            "project": project,
            "query": query,
            "limit": limit,
            "offset": offset,
        }.items()
        if value is not None
    }

    def text(data: dict[str, Any]) -> str:
        rows = [
            [
                str(t.get("id")),
                str(t.get("status")),
                str(t.get("assignee") or ""),
                str(t.get("title")),
            ]
            for t in data.get("items") or []
        ]
        more = (
            f"\n{data['total']} in all; next page: --offset {data['nextOffset']}"
            if data.get("nextOffset") is not None
            else ""
        )
        return (table(rows, ["ID", "STATUS", "ASSIGNEE", "TITLE"]) or "No tickets.") + more

    finish(current, lambda b, _o: call(b, "tasks", organization_id, **params), text)


@tickets_app.command("create")
def create_ticket(
    organization_id: str = typer.Argument(...),
    title: str = typer.Option(..., "--title"),
    description: str = typer.Option("", "--description"),
    assignee: str | None = typer.Option(None, "--assignee", help="Worker ID."),
    project: str | None = typer.Option(None, "--project", help="Project (initiative) ID."),
    status: str = typer.Option(
        "backlog",
        "--status",
        help="backlog (default) stays parked; todo lets a running organization pick it up.",
    ),
    priority: str | None = typer.Option(None, "--priority"),
    request_id: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Create a ticket. It starts in backlog unless you pass --status todo."""
    current = session("org tickets create", output, endpoint, token_file, data_dir)
    params: dict[str, Any] = {"title": title, "description": description, "status": status}
    for key, value in (("assignee", assignee), ("project", project), ("priority", priority)):
        if value:
            params[key] = value

    def create(backend: Backend, out: Output) -> Any:
        key = opts.request_id_value(request_id)
        return call(
            backend,
            "task_create",
            organization_id,
            **params,
            **({"idempotencyKey": key} if key else {}),
        )

    finish(
        current, create, lambda d: f"Created ticket {d.get('id') if isinstance(d, dict) else d}."
    )


@tickets_app.command("update")
def update_ticket(
    organization_id: str = typer.Argument(...),
    ticket_id: str = typer.Argument(...),
    expected_revision: int = typer.Option(
        ..., "--expected-revision", help="The ticket revision you read; stale ones exit 4."
    ),
    changes: list[str] = typer.Option(
        ..., "--set", help="Field change as name=value, such as status=todo. Repeatable."
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Change ticket fields: title, description, assignee, status, priority and more."""
    current = session("org tickets update", output, endpoint, token_file, data_dir)
    fields = res.parse_assignments(current, changes)
    finish(
        current,
        lambda b, _o: call(
            b,
            "task_update",
            organization_id,
            taskId=ticket_id,
            expectedRevision=expected_revision,
            changes=fields,
        ),
        lambda d: f"Updated ticket {ticket_id}.",
    )


@tickets_app.command("comment")
def comment_ticket(
    organization_id: str = typer.Argument(...),
    ticket_id: str = typer.Argument(...),
    body: str = typer.Option(..., "--body"),
    request_id: opts.RequestId = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Add a comment to a ticket."""
    current = session("org tickets comment", output, endpoint, token_file, data_dir)
    params: dict[str, Any] = {"taskId": ticket_id, "body": body}

    def comment(backend: Backend, out: Output) -> Any:
        key = opts.request_id_value(request_id)
        return call(
            backend,
            "comment",
            organization_id,
            **params,
            **({"idempotencyKey": key} if key else {}),
        )

    finish(current, comment, lambda d: f"Commented on {ticket_id}.")


@tickets_app.command("changes")
def ticket_changes(
    organization_id: str = typer.Argument(...),
    ticket_id: str = typer.Argument(...),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show the code changes a ticket's runs made."""
    current = session("org tickets changes", output, endpoint, token_file, data_dir)
    finish(current, lambda b, _o: call(b, "ticket_changes", organization_id, taskId=ticket_id))


# Knowledge -------------------------------------------------------------------------


@knowledge_app.command("search")
def search_knowledge(
    organization_id: str = typer.Argument(...),
    query: str = typer.Argument(""),
    limit: int = typer.Option(20, "--limit"),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Search shared organization knowledge."""
    current = session("org knowledge search", output, endpoint, token_file, data_dir)

    def text(data: Any) -> str:
        rows = [
            [str(e.get("id")), str(e.get("kind")), str(e.get("title"))]
            for e in data.get("items") or []
        ]
        return table(rows, ["ID", "KIND", "TITLE"]) or "Nothing found."

    finish(
        current,
        lambda b, _o: call(b, "knowledge_search", organization_id, query=query, limit=limit),
        text,
    )


@knowledge_app.command("read")
def read_knowledge(
    organization_id: str = typer.Argument(...),
    entry_id: str = typer.Argument(...),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Print one knowledge entry."""
    current = session("org knowledge read", output, endpoint, token_file, data_dir)
    finish(
        current,
        lambda b, _o: call(b, "knowledge_read", organization_id, id=entry_id),
        lambda d: (
            f"# {d.get('title')}\n\n{d.get('body') or d.get('summary') or ''}"
            if isinstance(d, dict)
            else json.dumps(d)
        ),
    )


@knowledge_app.command("save")
def save_knowledge(
    organization_id: str = typer.Argument(...),
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        help="JSON entry: {id?, kind, title, summary, body, topic, projectId, links}; - for stdin.",
    ),
    expected_version: int | None = typer.Option(None, "--expected-version", help="When updating."),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Create or update a knowledge entry."""
    current = session("org knowledge save", output, endpoint, token_file, data_dir)
    entry = res.read_json_file(current, file)
    if not isinstance(entry, dict):
        res.invalid(current, "The entry must be a JSON object")
    params: dict[str, Any] = {"entry": entry}
    if expected_version is not None:
        params["expectedVersion"] = expected_version
    finish(
        current,
        lambda b, _o: call(b, "knowledge_save", organization_id, **params),
        lambda d: f"Saved {d.get('id') if isinstance(d, dict) else ''}.",
    )
