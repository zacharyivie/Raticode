"""Providers, usage, projects, paired devices and live threads through the backend.

Providers' commands join the existing ``provider`` group next to ``provider profile``;
the rest are small groups of their own. All are reads, except provider settings and
steering or stopping a live thread turn.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import typer

from gofer.cli import client_options as opts
from gofer.cli.client import ExitCode, Output
from gofer.cli.commands import _resources as res
from gofer.cli.commands._backend import require_user
from gofer.cli.commands._resources import Backend, failure, finish, session, table

usage_app = typer.Typer(
    help="Provider usage and limits the backend has recorded.", no_args_is_help=True
)
projects_app = typer.Typer(
    help="Projects the backend knows about (read only).", no_args_is_help=True
)
devices_app = typer.Typer(
    help="Paired devices (read only; pairing stays in the app).", no_args_is_help=True
)
threads_app = typer.Typer(
    help=(
        "Follow, steer or stop a live thread turn by its IDs. The thread list itself "
        "lives in the desktop window, so listing and starting threads isn't available."
    ),
    no_args_is_help=True,
)

_PROVIDER_FIELDS = {"enabled", "executable", "defaultModel", "defaultEffort", "deniedModels"}


# Providers and models --------------------------------------------------------------


def list_providers(
    refresh: bool = typer.Option(False, "--refresh", help="Look for provider CLIs again."),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List providers: installed, enabled, version and default model."""
    current = session("provider list", output, endpoint, token_file, data_dir)

    def text(data: list[dict[str, Any]]) -> str:
        rows = [
            [
                str(p.get("id")),
                str(p.get("displayName") or ""),
                "yes" if p.get("available") else "no",
                "yes" if p.get("enabled") else "no",
                str(p.get("version") or ""),
                str(p.get("defaultModel") or ""),
            ]
            for p in data
        ]
        return table(rows, ["ID", "NAME", "INSTALLED", "ENABLED", "VERSION", "DEFAULT MODEL"])

    finish(
        current,
        lambda b, _o: b.request(
            "GET", "/provider/capabilities", query={"refresh": "1" if refresh else None}
        ).get("providers", []),
        text,
    )


def list_models(
    provider: str = typer.Argument(..., help="Provider ID from `provider list`."),
    refresh: bool = typer.Option(False, "--refresh", help="Ask the provider CLI again."),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List a provider's models and effort levels, and which models are blocked."""
    current = session("provider models", output, endpoint, token_file, data_dir)

    def fetch(backend: Backend, out: Output) -> dict[str, Any]:
        payload = backend.request(
            "GET",
            "/provider/capabilities",
            query={"provider": provider, "refresh": "1" if refresh else None},
        )
        providers = payload.get("providers") or []
        if not providers:
            raise failure(f"Unknown provider '{provider}'", ExitCode.NOT_FOUND)
        return dict(providers[0])

    def text(data: dict[str, Any]) -> str:
        denied = set(data.get("deniedModels") or [])
        rows = []
        for model in data.get("models") or []:
            model_id = str(model.get("id") if isinstance(model, dict) else model)
            label = str(model.get("label") or "") if isinstance(model, dict) else ""
            marks = []
            if model_id == data.get("defaultModel"):
                marks.append("default")
            if model_id in denied:
                marks.append("blocked")
            rows.append([model_id, label, ", ".join(marks)])
        return table(rows, ["MODEL", "LABEL", "NOTE"]) or "No models reported."

    finish(current, fetch, text)


def provider_status(
    provider: str = typer.Argument(...),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show whether a provider is signed in. Signing in happens in the app."""
    current = session("provider status", output, endpoint, token_file, data_dir)
    finish(current, lambda b, _o: b.request("GET", "/provider/auth", query={"provider": provider}))


def provider_set(
    provider: str = typer.Argument(...),
    values: list[str] = typer.Argument(
        ...,
        help=(
            "name=value pairs: enabled, executable, defaultModel, defaultEffort, "
            "deniedModels (a JSON list)."
        ),
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Change a provider's settings, such as its default model."""
    current = session("provider set", output, endpoint, token_file, data_dir)
    changes = res.parse_assignments(current, values)
    unknown = set(changes) - _PROVIDER_FIELDS
    if unknown:
        res.invalid(current, f"Unknown provider setting: {', '.join(sorted(unknown))}")

    def save(backend: Backend, out: Output) -> Any:
        require_user("change provider settings")
        return backend.request("POST", "/provider/settings", {"provider": provider, **changes})

    finish(current, save, lambda d: f"Saved {provider} settings.")


def register_provider_commands(app: typer.Typer) -> None:
    app.command("list")(list_providers)
    app.command("models")(list_models)
    app.command("status")(provider_status)
    app.command("set")(provider_set)


# Usage -----------------------------------------------------------------------------


@usage_app.command("show")
def show_usage(
    days: int = typer.Option(30, "--days", help="1, 7, 30 or 90."),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show recorded usage and limits per provider account. Asks no provider."""
    current = session("usage show", output, endpoint, token_file, data_dir)
    finish(
        current,
        lambda b, _o: b.request("GET", "/usage/overview", query={"days": days}),
        _usage_text,
    )


@usage_app.command("refresh")
def refresh_usage(
    days: int = typer.Option(30, "--days", help="1, 7, 30 or 90."),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Ask each signed-in provider for current usage, then show it."""
    current = session("usage refresh", output, endpoint, token_file, data_dir)
    finish(
        current,
        lambda b, _o: b.request("POST", "/usage/refresh", {}, query={"days": days}, timeout=180),
        _usage_text,
    )


def _usage_text(data: dict[str, Any]) -> str:
    rows = []
    for account in data.get("accounts") or []:
        windows = ", ".join(
            f"{w.get('label') or w.get('name')}: "
            f"{w.get('used_percent', w.get('usedPercent', '?'))}%"
            for w in account.get("windows") or []
        )
        rows.append(
            [
                str(account.get("provider") or account.get("id")),
                str(account.get("status") or ""),
                windows,
                str(account.get("observed_at") or ""),
            ]
        )
    return table(rows, ["PROVIDER", "STATUS", "LIMITS", "OBSERVED"]) or "No usage recorded."


# Projects --------------------------------------------------------------------------


@projects_app.command("list")
def list_projects(
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List project folders that hold registered workflows.

    The window's list of open projects is window state; this lists the projects the
    backend's workflow registry knows.
    """
    current = session("projects list", output, endpoint, token_file, data_dir)

    def fetch(backend: Backend, out: Output) -> list[dict[str, Any]]:
        projects: dict[str, dict[str, Any]] = {}
        for workflow in backend.request("GET", "/workflows").get("workflows", []):
            root = workflow.get("projectRoot")
            if not root:
                continue
            entry = projects.setdefault(
                root,
                {
                    "root": root,
                    "name": workflow.get("projectName") or Path(root).name,
                    "workflows": [],
                },
            )
            entry["workflows"].append(workflow.get("id"))
        return sorted(projects.values(), key=lambda item: str(item["root"]))

    def text(data: list[dict[str, Any]]) -> str:
        rows = [[str(p["name"]), str(len(p["workflows"])), str(p["root"])] for p in data]
        return table(rows, ["NAME", "WORKFLOWS", "FOLDER"]) or "No projects with workflows."

    finish(current, fetch, text)


# Devices ---------------------------------------------------------------------------


@devices_app.command("list")
def list_devices(
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List paired phones and computers and when each was last seen."""
    current = session("devices list", output, endpoint, token_file, data_dir)

    def text(data: dict[str, Any]) -> str:
        if data.get("error"):
            return str(data["error"])
        rows = [
            [
                str(p.get("name") or p.get("label") or p.get("id")),
                str(p.get("kind") or p.get("platform") or ""),
                str(p.get("state") or p.get("status") or ""),
                str(p.get("last_seen") or p.get("lastSeen") or ""),
            ]
            for p in data.get("peers") or []
        ]
        header = f"Device pairing {'on' if data.get('enabled') else 'off'}"
        return header + ("\n" + table(rows, ["NAME", "KIND", "STATE", "LAST SEEN"]) if rows else "")

    finish(current, lambda b, _o: b.request("GET", "/devices"), text)


# Threads ---------------------------------------------------------------------------


@threads_app.command("follow")
def follow_turn(
    conversation_id: str = typer.Argument(..., help="The thread's conversation ID."),
    turn_id: str = typer.Argument(..., help="The turn to follow."),
    after: int = typer.Option(0, "--after", help="Skip events up to this sequence number."),
    output: opts.Output = opts.OutputFormat.ndjson,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Stream a turn's events until it ends: one NDJSON event per line, then the result.

    Exit 3 if the turn is gone.
    """
    current = session("threads follow", output, endpoint, token_file, data_dir)

    def follow(backend: Backend, out: Output) -> dict[str, Any]:
        out.begin_stream()
        count = 0
        for event in backend.stream(
            "GET",
            "/chat/events",
            query={"conversationId": conversation_id, "turnId": turn_id, "after": after},
        ):
            out.event(event)
            count += 1
        return {"conversationId": conversation_id, "turnId": turn_id, "events": count}

    finish(current, follow, lambda d: None)


@threads_app.command("steering")
def steering_receipts(
    conversation_id: str = typer.Argument(...),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List the steering messages sent to a thread and whether each was delivered."""
    current = session("threads steering", output, endpoint, token_file, data_dir)
    finish(
        current,
        lambda b, _o: b.request("GET", "/chat/steering", query={"conversationId": conversation_id}),
    )


@threads_app.command("steer")
def steer_turn(
    conversation_id: str = typer.Argument(...),
    turn_id: str = typer.Argument(...),
    text_value: str = typer.Option(..., "--text", help="What to tell the running turn."),
    request_id: str | None = typer.Option(
        None, "--request-id", help="Retrying with the same ID never sends it twice."
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Send a message to a turn that is still running. Rem's own tasks use `rem` commands."""
    current = session("threads steer", output, endpoint, token_file, data_dir)
    body = {
        "conversationId": conversation_id,
        "turnId": turn_id,
        "requestId": request_id or str(uuid.uuid4()),
        "text": text_value,
    }
    finish(current, lambda b, _o: b.request("POST", "/chat/steer", body).get("receipt"))


@threads_app.command("stop")
def stop_turn(
    conversation_id: str = typer.Argument(...),
    turn_id: str = typer.Argument(..., help="The running turn."),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Stop a thread's running turn."""
    current = session("threads stop", output, endpoint, token_file, data_dir)
    finish(
        current,
        lambda b, _o: b.request(
            "POST", "/chat/stop", {"conversationId": conversation_id, "turnId": turn_id}
        ),
        lambda d: "Stopped.",
    )
