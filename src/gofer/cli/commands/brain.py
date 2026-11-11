"""Second Brain through a running Raticode backend: read, search, save and projects.

Adding or moving a brain folder needs a folder approval from the desktop, so it
stays in the app. Note files are plain files too; scripts can read them directly.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import typer

from gofer.cli import client_options as opts
from gofer.cli.client import Output
from gofer.cli.commands import _resources as res
from gofer.cli.commands._resources import Backend, failure, finish, session, table

app = typer.Typer(
    help="Second Brain through the running backend: list, search, read, save and projects.",
    no_args_is_help=True,
)

BRAIN = typer.Option(..., "--brain", help="Brain ID from `brain list`.")


def call(backend: Backend, action: str, **params: Any) -> Any:
    return backend.request("POST", "/second-brains", {"action": action, "params": params}).get(
        "result"
    )


@app.command("list")
def list_brains(
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List registered brains and whether their folders are reachable."""
    current = session("brain list", output, endpoint, token_file, data_dir)

    def text(data: list[dict[str, Any]]) -> str:
        rows = [
            [
                str(b.get("id")),
                str(b.get("name")),
                str(b.get("state")),
                str((b.get("index") or {}).get("documents", "")),
                str(b.get("root")),
            ]
            for b in data
        ]
        return table(rows, ["ID", "NAME", "STATE", "DOCS", "FOLDER"]) or "No brains registered."

    finish(current, lambda b, _o: call(b, "snapshot").get("brains", []), text)


@app.command("documents")
def list_documents(
    brain_id: str = BRAIN,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List a brain's indexed documents."""
    current = session("brain documents", output, endpoint, token_file, data_dir)
    fields = ("id", "title", "path", "type", "version", "project", "tags", "inbox")

    def fetch(backend: Backend, out: Output) -> list[dict[str, Any]]:
        documents = call(backend, "snapshot").get("documents", [])
        return [
            {key: doc.get(key) for key in fields}
            for doc in documents
            if doc.get("brainId") == brain_id and not doc.get("missing")
        ]

    def text(data: list[dict[str, Any]]) -> str:
        rows = [[str(d["id"]), str(d["title"]), str(d["path"])] for d in data]
        return table(rows, ["ID", "TITLE", "PATH"]) or "No documents."

    finish(current, fetch, text)


@app.command("search")
def search_brain(
    query: str = typer.Argument(...),
    brain_ids: list[str] = typer.Option(..., "--brain", help="Brain ID; repeat to search several."),
    mode: str = typer.Option(
        "exact", "--mode", help="exact (words), semantic (meaning) or hybrid."
    ),
    limit: int = typer.Option(20, "--limit"),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Search notes and documents. Each hit names a document and a block."""
    current = session("brain search", output, endpoint, token_file, data_dir)

    def fetch(backend: Backend, out: Output) -> dict[str, Any]:
        result = dict(call(backend, "search", brainIds=brain_ids, query=query, mode=mode))
        result["hits"] = list(result.get("hits") or [])[:limit]
        return result

    def text(data: dict[str, Any]) -> str:
        rows = [
            [
                str(h.get("docId")),
                str(h.get("blockId")),
                f"{float(h.get('score') or 0):.2f}",
                str(h.get("matchKind") or ""),
            ]
            for h in data["hits"]
        ]
        return table(rows, ["DOCUMENT", "BLOCK", "SCORE", "MATCH"]) or "Nothing found."

    finish(current, fetch, text)


@app.command("read")
def read_document(
    document_id: str = typer.Argument(...),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Print a document's original text. PDFs come back base64-encoded in JSON."""
    current = session("brain read", output, endpoint, token_file, data_dir)
    finish(
        current,
        lambda b, _o: call(b, "original", id=document_id),
        lambda d: (
            str(d.get("content")) if d.get("content") is not None else "(binary; use --format json)"
        ),
    )


@app.command("save")
def save_note(
    brain_id: str = BRAIN,
    title: str = typer.Option(..., "--title"),
    file: Path | None = typer.Option(
        None, "--file", "-f", help="Text to save; - for stdin. Defaults to --text."
    ),
    text_value: str | None = typer.Option(None, "--text", help="Text to save."),
    source_url: str = typer.Option(
        "", "--url", help="Where the text came from; recorded, not fetched."
    ),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait until it's indexed."),
    timeout: opts.Timeout = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Save text into a brain's Inbox as a new note and index it."""
    current = session("brain save", output, endpoint, token_file, data_dir)
    limit = res.duration(current, timeout, 120)
    if (file is None) == (text_value is None):
        res.invalid(current, "Give exactly one of --file or --text")
    if file is not None:
        try:
            content = sys.stdin.read() if str(file) == "-" else file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            res.invalid(current, f"Could not read {file}: {exc}")
    else:
        content = str(text_value)
    if not content.strip():
        res.invalid(current, "There is no text to save")

    def save(backend: Backend, out: Output) -> dict[str, Any]:
        # The url is only recorded: an excerpt is given, so the backend never fetches it.
        task = dict(
            call(
                backend,
                "task",
                kind="capture",
                brainId=brain_id,
                title=title,
                excerpt=content,
                url=source_url,
            )
        )
        if not wait:
            return task

        def check() -> tuple[bool, dict[str, Any]]:
            tasks = call(backend, "snapshot").get("tasks", [])
            found = next((t for t in tasks if t.get("id") == task.get("id")), task)
            return found.get("state") in {"done", "failed", "cancelled"}, found

        finished = dict(res.poll(check, timeout=limit, interval=1.0))
        if finished.get("state") != "done":
            raise failure(str(finished.get("error") or "Saving the note failed"), data=finished)
        return finished

    def text(data: dict[str, Any]) -> str:
        result = data.get("result") or {}
        if result.get("path"):
            return f"Saved {result['path']} ({result.get('docId')})."
        return f"Saving as task {data.get('id')} ({data.get('state')})."

    finish(current, save, text)


@app.command("projects")
def brain_projects(
    brain_id: str = BRAIN,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List the projects a brain's notes and reports belong to."""
    current = session("brain projects", output, endpoint, token_file, data_dir)

    def text(data: Any) -> str:
        items = data.get("projects", []) if isinstance(data, dict) else data
        rows = [
            [
                str(p.get("id")),
                str(p.get("name")),
                str(p.get("documents") or p.get("documentCount") or ""),
            ]
            for p in items or []
        ]
        return table(rows, ["ID", "NAME", "DOCS"]) or "No projects."

    finish(current, lambda b, _o: call(b, "projects", brainId=brain_id), text)
