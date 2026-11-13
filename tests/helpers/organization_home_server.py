"""Isolated backend with a seeded organization for the Organizations UI test.

Everything lives in a temporary folder: a Git project, a Second Brain folder and the
Raticode data directory. The employee is a test double that uses the real organization
tools: it reads a note, commits a change and posts claims that Raticode then checks.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from gofer.ui.server import create_server

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
}


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, env=GIT_ENV, capture_output=True, text=True
    ).stdout.strip()


def seed(server: Any, root: Path, project: Path, brain: Path) -> dict[str, Any]:
    manager = server.organizations

    def call(action: str, oid: str | None = None, actor: str = "user", **params: Any) -> Any:
        arguments: dict[str, Any] = {"action": action, "params": params}
        if oid:
            arguments["organizationId"] = oid
        return manager.call(str(project), actor, arguments)

    org = call(
        "create",
        config={
            "name": "Raticode Studio",
            "slug": "studio",
            "description": "Builds Raticode",
            "remSecondBrain": {"enabled": True, "root": str(brain)},
            # The seed's Backlog stays put while its one run goes; the test turns pulls on.
            "pullBacklog": False,
            "employees": [
                {
                    "id": "avery",
                    "name": "Avery Lane",
                    "title": "Director",
                    "persona": "Calm planner. Keeps tickets small enough to review in minutes.",
                    "responsibilities": ["Plan milestones", "Split epics into tickets"],
                    "provider": "claude_code",
                },
                {
                    "id": "mira",
                    "name": "Mira Santos",
                    "title": "Desktop engineer",
                    "reportsTo": "avery",
                    "persona": "Ships small, tested slices.",
                    "responsibilities": ["Desktop UI and backend", "Regression tests"],
                },
                {
                    "id": "sage",
                    "name": "Sage Kim",
                    "title": "Researcher",
                    "reportsTo": "avery",
                    "paused": True,
                    "persona": "Reads the source before the summary.",
                    "responsibilities": ["Cite sources"],
                },
            ],
        },
    )
    oid = org["id"]

    def plan(kind: str, **item: Any) -> None:
        revision = call("read", oid)["revision"]
        call("plan_save", oid, kind=kind, item=item, expectedRevision=revision)

    plan("mission", mission="Make agent work something you can trust at a glance.")
    plan(
        "project",
        id="desktop",
        name="Raticode Desktop",
        owner="mira",
        color="#215879",
        checks=[{"name": "unit", "command": "echo '3 passed'"}],
        autoRunChecks=True,
    )
    plan("project", id="docs", name="Docs site", owner="sage", color="#6a4bb0")
    today = time.strftime("%Y-%m-%d")
    plan(
        "milestone",
        id="M1",
        title="Every agent claim has evidence",
        short="Evidence",
        dueAt=f"{int(today[:4]) + 1}-01-31",
    )
    plan(
        "milestone", id="M2", title="Raticode 1.0", short="1.0", dueAt=f"{int(today[:4]) + 1}-03-20"
    )
    plan(
        "milestone",
        id="PM1",
        title="Organizations 2.0",
        projectId="desktop",
        parentId="M1",
        dueAt=f"{int(today[:4]) + 1}-01-15",
    )
    plan(
        "milestone",
        id="PM2",
        title="Docs for 1.0",
        projectId="docs",
        parentId="M2",
        dueAt=f"{int(today[:4]) + 1}-03-01",
    )
    plan("epic", id="E1", title="Ticket board", milestoneId="PM1", owner="mira")
    plan("epic", id="E2", title="Evidence receipts", milestoneId="PM1", owner="mira")
    plan("epic", id="E3", title="Organizations guide", milestoneId="PM2", owner="sage")

    board = call(
        "knowledge_save",
        oid,
        entry={
            "kind": "decision",
            "title": "The board has five fixed columns",
            "topic": "Board",
            "projectId": "desktop",
            "body": "Backlog, To do, Running, Review, Done. Only you move work into Done.",
        },
    )
    cards = call(
        "knowledge_save",
        oid,
        entry={
            "kind": "spec",
            "title": "Card anatomy: key, title, owner, badges",
            "topic": "Board",
            "projectId": "desktop",
            "body": "No descriptions on cards. Evidence state and links are the only badges.",
            "links": [board["id"]],
        },
    )
    claims = call(
        "knowledge_save",
        oid,
        entry={
            "kind": "spec",
            "title": "Claims: 120 characters, five per run",
            "topic": "Evidence",
            "body": "Short claims with evidence replace long reports. Raticode checks each one.",
        },
    )
    (brain / "Research").mkdir(parents=True, exist_ok=True)
    (brain / "Research" / "logging.md").write_text(
        "# Logs never include tokens\n\nDevice names and timings are fine. Tokens and message "
        "text never are.\n"
    )
    imported = call(
        "knowledge_import", oid, path="Research/logging.md", kind="decision", topic="Logs"
    )
    call(
        "knowledge_link",
        oid,
        **{"from": claims["id"], "to": imported["id"], "quote": "Raticode checks each one"},
    )

    def ticket(title: str, epic: str, status: str = "todo", **more: Any) -> Any:
        return call("task_create", oid, title=title, epicId=epic, status=status, **more)

    done = ticket("Five-column board with one-line cards", "E1", "backlog", assignee="mira")
    drag = ticket(
        "Drag cards between columns",
        "E1",
        assignee="mira",
        doneWhen="A card moves by drag or keyboard and the move is recorded.",
        knowledge=[board["id"], cards["id"]],
    )
    ticket("Group the board by epic", "E1", "backlog", assignee="mira")
    ticket(
        "Flag claims whose source changed",
        "E2",
        "backlog",
        assignee="mira",
        knowledge=[claims["id"]],
    )
    ticket("Write the Organizations guide", "E3", "backlog", assignee="sage")
    call(
        "task_update",
        oid,
        taskId=done["id"],
        expectedRevision=done["revision"],
        changes={"status": "done", "evidence": "Shipped in the seed"},
    )
    call("control", oid, state="running")
    until = time.monotonic() + 30
    while time.monotonic() < until:
        state = call("read", oid)
        current = next(t for t in state["runtime"]["tasks"] if t["id"] == drag["id"])
        claims_now = [c for c in state["runtime"]["claims"].values() if c["taskId"] == drag["id"]]
        if current["status"] == "in_review" and any(
            c["evidence"].get("state") == "ok" and c["evidence"].get("kind") == "check"
            for c in claims_now
        ):
            break
        time.sleep(0.2)
    else:
        raise RuntimeError("The seeded run did not finish")
    call("control", oid, state="paused")
    return {"organizationId": oid, "reviewTicket": drag["id"], "boardNote": board["id"]}


def employee(holder: dict[str, Any]) -> Any:
    async def run(**options: Any) -> AsyncGenerator[dict[str, Any], None]:
        manager = holder["server"].organizations
        scope = options["workflow"]["organizationRun"]
        oid = scope["organizationId"]
        state = manager.store.get("", oid)
        context = {
            "employee_id": "mira",
            "bound_org": oid,
            "generation": state["runtime"]["generation"],
            "run_id": scope["parentRunId"],
        }

        def tool(action: str, **params: Any) -> Any:
            return manager.call(
                str(holder["project"]),
                "employee:mira",
                {"action": action, "organizationId": oid, "params": params},
                **context,
            )

        plan = json.loads(options["messages"][0]["body"])["plan"]
        note = tool("knowledge_search", query="five fixed columns")["items"][0]
        tool("knowledge_read", id=note["id"])
        yield {
            "type": "thought",
            "text": "Read",
            "trace": {
                "id": "t1",
                "kind": "tool",
                "title": "organization_action",
                "input": {"action": "knowledge_read"},
            },
        }
        work = Path(options["working_dir"])
        (work / "board.js").write_text("export const columns = 5;\n")
        git(work, "add", ".")
        git(work, "commit", "-qm", "Drag cards between columns")
        sha = git(work, "rev-parse", "HEAD")
        yield {
            "type": "thought",
            "text": "Edit",
            "trace": {
                "id": "t2",
                "kind": "tool",
                "title": "Edit",
                "input": {"file_path": "board.js"},
            },
        }
        yield {
            "type": "thought",
            "text": "Test",
            "trace": {
                "id": "t3",
                "kind": "tool",
                "title": "Bash",
                "shell": "bash",
                "input": {"command": "npm test -- board"},
            },
        }
        key = plan["ticket"]
        tool(
            "claim_add",
            taskId=key,
            text="Board tests pass",
            evidence={"kind": "check", "command": "unit", "reported": "passed"},
        )
        tool(
            "claim_add",
            taskId=key,
            text="Follows the five-column decision",
            evidence={
                "kind": "quote",
                "entryId": note["id"],
                "quote": "Only you move work into Done",
            },
        )
        tool(
            "claim_add",
            taskId=key,
            text="Committed the board change",
            evidence={"kind": "commit", "sha": sha},
        )
        tool("claim_add", taskId=key, text="Feels faster on a real machine")
        yield {
            "type": "thought",
            "text": "Claims",
            "trace": {
                "id": "t4",
                "kind": "tool",
                "title": "organization_action",
                "input": {"action": "claim_add"},
            },
        }
        yield {"type": "final", "message": {"body": "Cards move by drag; tests pass."}}

    return run


if __name__ == "__main__":
    os.environ["RATICODE_EXPERIMENTAL_ORGANIZATIONS"] = "1"
    keep = os.environ.get("RATICODE_ORG_HOME_DIR")
    directory = keep or tempfile.mkdtemp(prefix="organization-home-")
    root = Path(directory)
    project = root / "project"
    brain = root / "brain"
    project.mkdir(parents=True, exist_ok=True)
    brain.mkdir(exist_ok=True)
    git(project, "init", "-q", "-b", "main")
    git(project, "commit", "-q", "--allow-empty", "-m", "Start")
    holder: dict[str, Any] = {"project": project}
    server = create_server(
        port=int(os.environ.get("RATICODE_ORG_HOME_PORT", "0")),
        data_dir=root,
        api_token="organization-browser-test",
    )
    holder["server"] = server
    server.organizations.stream = employee(holder)
    import threading

    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        seeded = seed(server, root, project, brain)
        print(
            json.dumps(
                {
                    "port": server.server_address[1],
                    "project": str(project),
                    "root": str(root),
                    **seeded,
                }
            ),
            flush=True,
        )
        sys.stdin.read() if os.environ.get("RATICODE_ORG_HOME_STDIN") else threading.Event().wait()
    finally:
        server.shutdown()
        server.server_close()
