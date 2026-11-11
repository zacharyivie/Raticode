"""Swarms through a running Raticode backend: read and configure only.

Starting, pausing, resuming or messaging a swarm runs agents, so those stay in the
desktop app. Swarms belong to a project folder, which every command needs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from gofer.cli import client_options as opts
from gofer.cli.client import Output, confirm
from gofer.cli.commands import _resources as res
from gofer.cli.commands._backend import require_user
from gofer.cli.commands._resources import Backend, finish, quote, session, table

app = typer.Typer(
    help=(
        "Swarms through the running backend: list, show, history, and configuration "
        "as files. Starting and controlling swarms stays in the desktop app."
    ),
    no_args_is_help=True,
)

PROJECT = typer.Option(..., "--project", help="The project folder the swarm belongs to.")

# What a swarm file carries; run state, history and IDs stay with the backend.
CONFIG_FIELDS = (
    "name",
    "charter",
    "agents",
    "wakeIntervalSeconds",
    "maxConcurrency",
    "maxTokens",
    "maxTurns",
    "maxRunSeconds",
    "gitPermissions",
    "integrationChecks",
)


def _root(project: Path) -> str:
    return str(project.expanduser().resolve())


def _get(backend: Backend, project: Path, swarm_id: str, history: bool = False) -> dict[str, Any]:
    payload = backend.request(
        "GET",
        f"/swarms/{quote(swarm_id)}",
        query={"projectRoot": _root(project), "history": "summary" if history else "0"},
    )
    return dict(payload.get("swarm") or {})


@app.command("list")
def list_swarms(
    project: Path = PROJECT,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List a project's swarms."""
    current = session("swarm list", output, endpoint, token_file, data_dir)

    def text(data: list[dict[str, Any]]) -> str:
        rows = [
            [
                str(s.get("id")),
                str(s.get("name")),
                str((s.get("run") or {}).get("state") or s.get("state") or "idle"),
                str(len(s.get("agents") or []) or s.get("agentCount") or ""),
            ]
            for s in data
        ]
        return table(rows, ["ID", "NAME", "STATE", "AGENTS"]) or "No swarms in this project."

    finish(
        current,
        lambda b, _o: b.request("GET", "/swarms", query={"projectRoot": _root(project)}).get(
            "swarms", []
        ),
        text,
    )


@app.command("show")
def show_swarm(
    swarm_id: str = typer.Argument(...),
    project: Path = PROJECT,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show a swarm's configuration and current run."""
    current = session("swarm show", output, endpoint, token_file, data_dir)

    def text(data: dict[str, Any]) -> str:
        run = data.get("run") or {}
        lines = [f"{data.get('name')} ({data.get('id')})", f"Run: {run.get('state') or 'none'}"]
        for agent in data.get("agents") or []:
            lines.append(
                f"Agent: {agent.get('name') or agent.get('id')} "
                f"({agent.get('provider')}, {agent.get('model') or 'default model'})"
            )
        return "\n".join(lines)

    finish(current, lambda b, _o: _get(b, project, swarm_id), text)


@app.command("history")
def swarm_history(
    swarm_id: str = typer.Argument(...),
    project: Path = PROJECT,
    run_id: str | None = typer.Option(None, "--run", help="Show one past run in full."),
    offset: int = typer.Option(0, "--offset"),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List a swarm's past runs (20 at a time), or show one."""
    current = session("swarm history", output, endpoint, token_file, data_dir)
    query: dict[str, Any] = {"projectRoot": _root(project)}
    if run_id:
        query["runId"] = run_id
    else:
        query["offset"] = offset

    def text(data: dict[str, Any]) -> str:
        if "run" in data:
            return json.dumps(data["run"], indent=2)
        rows = [
            [
                str(r.get("id")),
                str(r.get("state")),
                str(r.get("startedAt") or ""),
                str(r.get("task") or "")[:60],
            ]
            for r in data.get("history", [])
        ]
        return table(rows, ["RUN", "STATE", "STARTED", "TASK"]) or "No past runs."

    finish(
        current,
        lambda b, _o: b.request("GET", f"/swarms/{quote(swarm_id)}/history", query=query),
        text,
    )


@app.command("export")
def export_swarm(
    swarm_id: str = typer.Argument(...),
    project: Path = PROJECT,
    file: Path | None = typer.Option(
        None, "--file", "-f", help="File to write; default the envelope's data."
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Write a swarm's configuration as a JSON file for `swarm apply`."""
    current = session("swarm export", output, endpoint, token_file, data_dir)

    def fetch(backend: Backend, out: Output) -> dict[str, Any]:
        swarm = _get(backend, project, swarm_id)
        document = {
            "swarmId": swarm.get("id"),
            "config": {key: swarm[key] for key in CONFIG_FIELDS if key in swarm},
        }
        if file is not None:
            res.write_json_file(file, document)
        return document

    finish(current, fetch, lambda d: f"Wrote {file}" if file else json.dumps(d, indent=2))


@app.command("apply")
def apply_swarm(
    project: Path = PROJECT,
    file: Path = typer.Option(..., "--file", "-f", help="File from `swarm export`; - for stdin."),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Update a swarm from a file, or create it when the file has no swarmId.

    This never starts the swarm. A running swarm must be paused in the app first.
    Creating one needs a project folder the backend trusts.
    """
    current = session("swarm apply", output, endpoint, token_file, data_dir)
    document = res.read_json_file(current, file)
    if not isinstance(document, dict) or not isinstance(document.get("config"), dict):
        res.invalid(current, "The file needs a config object (see `swarm export`)")
    config = {key: value for key, value in document["config"].items() if key in CONFIG_FIELDS}
    body = {**config, "projectRoot": _root(project)}
    swarm_id = document.get("swarmId")

    def apply(backend: Backend, out: Output) -> dict[str, Any]:
        # A swarm configuration decides each agent's access.
        require_user("change a swarm's configuration")
        if swarm_id:
            result = backend.request("PUT", f"/swarms/{quote(str(swarm_id))}", body)
        else:
            result = backend.request("POST", "/swarms", body)
        return dict(result.get("swarm") or {})

    finish(current, apply, lambda d: f"Saved swarm {d.get('name')} ({d.get('id')}).")


@app.command("delete")
def delete_swarm(
    swarm_id: str = typer.Argument(...),
    project: Path = PROJECT,
    yes: opts.Yes = False,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Delete a stopped swarm and its run records. Its git worktrees and branches stay."""
    current = session("swarm delete", output, endpoint, token_file, data_dir)

    def delete(backend: Backend, out: Output) -> Any:
        require_user("delete a swarm")
        confirm(f"Delete swarm {swarm_id} and its run records?", yes=yes)
        return backend.request(
            "DELETE", f"/swarms/{quote(swarm_id)}", query={"projectRoot": _root(project)}
        )

    finish(current, delete, lambda d: f"Deleted swarm {swarm_id}.")
