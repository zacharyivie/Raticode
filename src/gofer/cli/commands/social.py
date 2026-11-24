"""``Raticode social``: propose and read social posts, through the backend.

There is no approve or publish command. Approving a public post is a click in the Posts
dashboard, so a script, workflow or agent can't approve one. A proposal from this command
is recorded as ``cli``, ``workflow:<source>`` with ``--source``, or ``rem`` inside an
agent turn, never as the user, so it waits for review like any other.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import typer

from gofer.cli import client_options as opts
from gofer.cli.client import Output
from gofer.cli.commands import _resources as res
from gofer.cli.commands._backend import agent_session
from gofer.cli.commands._resources import Backend, finish, session

app = typer.Typer(
    help=(
        "Social posts: propose posts for your review and read their state. Approving and "
        "publishing happen in the app's Posts dashboard."
    ),
    no_args_is_help=True,
)
MAX_FILE_BYTES = 256 * 1024
_SOURCE = re.compile(r"[\w.@:-]{1,80}")


def _post_line(post: dict[str, Any]) -> str:
    when = post.get("scheduled_at") or "when approved"
    platforms = ", ".join(
        f"{v.get('platform_label', v.get('platform'))} ({v.get('state')})"
        for v in post.get("variants") or []
    )
    return f"{post['id']}  {post['state']:<16} {when}  {post.get('title', '')}  [{platforms}]"


def _post_text(post: dict[str, Any]) -> str:
    lines = [_post_line(post), f"Proposed by: {post.get('created_by')}"]
    for flag in post.get("flags") or []:
        lines.append(f"! {flag.get('message')}")
    for variant in post.get("variants") or []:
        lines.append(f"\n--- {variant.get('account_name')} ({variant.get('state')})")
        lines.append(str(variant.get("text") or ""))
        if variant.get("link"):
            lines.append(str(variant["link"]))
        options = variant.get("options") or {}
        for key in sorted(options):
            if options[key] not in (None, ""):
                lines.append(f"{key}: {options[key]}")
        if variant.get("message"):
            lines.append(f"Note: {variant['message']}")
    return "\n".join(lines)


@app.command("propose")
def propose(
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        help="JSON file with title, scheduled_at, timezone, rationale, sources and variants. "
        "Use - for standard input.",
    ),
    source: str | None = typer.Option(
        None, "--source", help="Name the workflow or script proposing, shown on the card."
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Propose a post for your review. It isn't published until you approve it in the app."""
    current = session("social propose", output, endpoint, token_file, data_dir)
    try:
        raw = sys.stdin.read(MAX_FILE_BYTES + 1) if str(file) == "-" else None
        if raw is None:
            if file.stat().st_size > MAX_FILE_BYTES:
                res.invalid(current, "The post file is larger than 256 KB.")
            raw = file.read_text(encoding="utf-8")
        body = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        res.invalid(current, f"Couldn't read the post file: {type(exc).__name__}")
    if not isinstance(body, dict):
        res.invalid(current, "The post file must hold one JSON object.")
    if source is not None and not _SOURCE.fullmatch(source):
        res.invalid(current, "--source is up to 80 letters, digits, '.', '@', ':' or '-'.")
    if agent_session():
        body["proposer"] = "rem"
    else:
        body["proposer"] = f"workflow:{source}" if source else "cli"

    def run(backend: Backend, _out: Output) -> Any:
        return backend.request("POST", "/social/posts", body)

    finish(current, run, _post_text)


@app.command("list")
def list_posts(
    state: str | None = typer.Option(None, "--state", help="Only posts in this state."),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List recent posts with their state and platforms."""
    current = session("social list", output, endpoint, token_file, data_dir)
    # Encoded, so a state can't add other query parameters.
    path = "/social/posts" + (f"?{urlencode({'state': state})}" if state else "")

    def run(backend: Backend, _out: Output) -> Any:
        return backend.request("GET", path)

    finish(
        current, run, lambda d: "\n".join(_post_line(p) for p in d.get("posts", [])) or "No posts."
    )


@app.command("show")
def show(
    post_id: str = typer.Argument(..., help="The post's id, such as post-1a2b3c4d5e6f."),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show one post: every platform's text, state, flags and history."""
    current = session("social show", output, endpoint, token_file, data_dir)
    if not re.fullmatch(r"post-[0-9a-f]{12}", post_id):
        res.invalid(current, "Post ids look like post-1a2b3c4d5e6f.")

    def run(backend: Backend, _out: Output) -> Any:
        return backend.request("GET", f"/social/posts/{post_id}")

    finish(current, run, _post_text)


@app.command("accounts")
def accounts(
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List the accounts proposals may target. Logins are never shown."""
    current = session("social accounts", output, endpoint, token_file, data_dir)

    def run(backend: Backend, _out: Output) -> Any:
        return backend.request("GET", "/social/rules")

    def text(data: Any) -> str:
        lines = [
            f"{a['account_id']}  {a['platform']:<9} {a['name']}" for a in data.get("accounts", [])
        ]
        lines += [
            f"{m['platform']:<22} manual: {m['note']}" for m in data.get("manual_platforms", [])
        ]
        return "\n".join(lines)

    finish(current, run, text)


@app.command("templates")
def templates(
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List post templates and their blanks. Propose from one with template and fields."""
    current = session("social templates", output, endpoint, token_file, data_dir)

    def run(backend: Backend, _out: Output) -> Any:
        return backend.request("GET", "/social/templates")

    def text(data: Any) -> str:
        lines = []
        for item in data.get("templates", []):
            kind = "starter" if item.get("builtin") else "yours"
            blanks = ", ".join(item.get("blanks") or []) or "no blanks"
            lines.append(f"{item['id']}  {kind:<7} {item['name']}  [{blanks}]")
        return "\n".join(lines) or "No templates."

    finish(current, run, text)


@app.command("times")
def times(
    targets: str = typer.Option(
        "",
        "--for",
        help="Comma-separated account ids or platforms, such as x,linkedin.",
    ),
    timezone: str | None = typer.Option(
        None, "--tz", help="Time zone name, such as America/New_York. Default: this computer's."
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Suggest good times to post, skipping times close to other planned posts."""
    from urllib.parse import urlencode

    current = session("social times", output, endpoint, token_file, data_dir)
    query: dict[str, str] = {"target": targets}
    if timezone:
        query["tz"] = timezone

    def run(backend: Backend, _out: Output) -> Any:
        return backend.request("GET", "/social/times?" + urlencode(query))

    def text(data: Any) -> str:
        lines = [f"{slot['at']}  {slot['reason']}" for slot in data.get("slots", [])]
        return "\n".join([*lines, data.get("note", "")]).strip()

    finish(current, run, text)
