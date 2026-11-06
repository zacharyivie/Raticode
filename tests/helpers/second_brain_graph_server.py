"""Isolated Second Brain backend with projects, report history and an organization.

Seeds a temporary brain with report folders, a Rem task receipt, an archived Rem
conversation and a fake organization ticket, so the desktop test can check projects,
connections and the topic map against the real library service.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

from gofer.ui.server import create_server

REPO = "/work/Raticode/Raticode"


def page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html><head><title>{title}</title></head>"
        f"<body><p>{body}</p></body></html>"
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="second-brain-graph-") as directory:
        data = Path(directory)
        brain = data / "vault"
        files = {
            "Reports/Raticode/organizations-audit.html": page(
                "Organizations audit", "Tickets approvals consent policy audit organizations."
            ),
            "Reports/Raticode/organizations-audit-v2.html": page(
                "Organizations audit (revised)", "Tickets approvals consent policy audit revised."
            ),
            "Reports/Raticode/organizations-design.html": page(
                "Organizations design", "Tickets kanban consent policy evidence organizations."
            ),
            "Reports/Taskurotta/release-signing.html": page(
                "Release signing", "Notarization signing release installer packaging."
            ),
            "Reports/Taskurotta/release-checklist.html": page(
                "Release checklist", "Release installer packaging notarization checklist."
            ),
            "Reports/Taskurotta/release-windows.html": page(
                "Windows release", "Windows installer signing packaging release notarization."
            ),
            "reports/rem/Reports/Raticode/search-repair.html": page(
                "Search repair", "Vector search embedding cosine ranking repair index."
            ),
            "Notes/embedding-notes.md": (
                "# Embedding notes\n\nVector embedding cosine ranking index search notes.\n\n"
                "See [[Notes/ranking]].\n"
            ),
            "Notes/ranking.md": "# Ranking\n\nCosine ranking vector search embedding index.\n",
            "Inbox/garden.md": "# Garden\n\nTomatoes basil and peppers this spring.\n",
        }
        for relative, text in files.items():
            path = brain / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        archive = brain / "Rem Conversations"
        (archive / "threads").mkdir(parents=True)
        (archive / "README.raticode.md").write_text("# Rem conversation archive\n")
        (archive / "threads/t1.json").write_text(json.dumps({"messages": []}))
        (archive / "index.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "threads": {
                        "t1": {
                            "threadId": "thread-1",
                            "title": "Plan the organizations audit",
                            "projectRoot": REPO,
                            "updatedAt": "2026-11-04T10:00:00Z",
                            "messageCount": 6,
                            "snapshot": "threads/t1.json",
                        }
                    },
                }
            )
        )
        target = brain / "reports/rem/Reports/Raticode/search-repair.html"
        db = sqlite3.connect(data / "rem-home.sqlite3")
        db.executescript(
            "CREATE TABLE runs(id TEXT, task TEXT, occurrence TEXT, state TEXT, body TEXT);"
            "CREATE TABLE tasks(id TEXT, body TEXT);"
        )
        db.execute(
            "INSERT INTO runs VALUES('run-1','task-1','','succeeded',?)",
            (
                json.dumps(
                    {
                        "id": "run-1",
                        "task_id": "task-1",
                        "state": "succeeded",
                        "recipe": {"title": "Repair vector search", "project_dir": REPO},
                        "config": {"provider": "claude_code", "model": "claude-opus-5-5"},
                        "finished_at": "2026-11-05T09:00:00+00:00",
                        "artifacts": [
                            {
                                "path": str(target),
                                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                                "epoch": 0,
                            }
                        ],
                    }
                ),
            ),
        )
        db.commit()
        db.close()
        organization = {
            "id": "org-1",
            "config": {"name": "Lab", "slug": "lab", "projects": []},
            "runtime": {
                "tasks": [{"id": "task-3", "number": 3, "title": "Tune ranking", "claim": ""}],
                "knowledge": {
                    "k1": {
                        "id": "k1",
                        "title": "Ranking",
                        "summary": "",
                        "body": "Cosine ranking vector search embedding index.",
                        "version": 1,
                        "source": {"path": str(brain / "Notes/ranking.md")},
                    }
                },
                "links": {"l1": {"kind": "read", "from": "ticket:task-3", "to": "kb:k1"}},
                "claims": {},
            },
        }
        server = create_server(port=0, data_dir=data, api_token="second-brain-browser-test")
        server.second_brains.organization_source = lambda: [organization]
        print(json.dumps({"port": server.server_address[1], "root": str(brain)}), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
