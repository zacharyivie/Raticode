"""Isolated backend with Rem's consent requests waiting, for the Approve all UI test.

Everything lives in a temporary folder. The organization is created paused with a
read-only lead, two tickets that need approval and a hold on a second worker; then Rem
asks, in an unsafe order, to start it, to remove both tickets' approval steps and to
give the lead write access. Each line ``ask`` on stdin makes Rem ask for one more thing
(resume the held worker) and prints its request ID, so the test can check that a request
made after the confirmation list opened is never approved.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from gofer.ui.server import create_server


async def finish(**_: Any) -> Any:
    """A stand-in employee: approving the start must never run a real provider."""
    yield {"type": "final", "message": {"body": "Done."}}


def main() -> None:
    os.environ["RATICODE_EXPERIMENTAL_ORGANIZATIONS"] = "1"
    root = Path(tempfile.mkdtemp(prefix="organization-requests-"))
    project = root / "project"
    project.mkdir()
    server = create_server(port=0, data_dir=root, api_token="organization-browser-test")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    manager = server.organizations
    manager.stream = finish

    def call(action: str, oid: str | None, actor: str, **params: Any) -> Any:
        arguments: dict[str, Any] = {"action": action, "params": params}
        if oid:
            arguments["organizationId"] = oid
        return manager.call(str(project), actor, arguments)

    try:
        org = call(
            "create",
            None,
            "user",
            config={
                "name": "Paper Desk",
                # Backlog tickets that stay put, so starting runs nothing.
                "pullBacklog": False,
                "employees": [
                    {"id": "lead", "name": "Director", "permissionMode": "read-only"},
                    {"id": "builder", "name": "Builder", "paused": True},
                ],
            },
        )
        oid = org["id"]
        tickets = [
            call(
                "task_create",
                oid,
                "user",
                title=title,
                assignee="lead",
                approvalRequired=True,
                status="backlog",
            )
            for title in ("Draft the charter", "Set paper risk limits")
        ]
        rem = "Rem:thread"
        call("control", oid, rem, state="running")
        for ticket in tickets:
            call(
                "task_update",
                oid,
                rem,
                taskId=ticket["id"],
                expectedRevision=ticket["revision"],
                changes={"approvalRequired": False},
            )
        current = manager.store.get("", oid)
        config = copy.deepcopy(current["config"])
        config["employees"][0]["permissionMode"] = "workspace-write"
        call(
            "configure",
            oid,
            rem,
            config=config,
            expectedRevision=current["revision"],
            reason="Let the Director write",
        )
        print(json.dumps({"port": server.server_address[1], "organizationId": oid}), flush=True)
        for line in sys.stdin:
            if line.strip() != "ask":
                continue
            current = manager.store.get("", oid)
            config = copy.deepcopy(current["config"])
            config["employees"][1]["paused"] = False
            asked = call(
                "configure",
                oid,
                rem,
                config=config,
                expectedRevision=current["revision"],
                reason="Resume the Builder",
            )
            print(json.dumps({"asked": asked["requestId"]}), flush=True)
    finally:
        server.shutdown()
        server.server_close()
        manager.close()
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
