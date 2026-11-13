"""The user's chat message to Rem approves Organizations requests they name.

Fake providers and isolated data only (test_rem_kanban's ``board`` fixture plus a private
OrganizationManager). Nothing here touches a real organization.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from gofer.ui.organizations import OrganizationManager
from tests.unit.test_rem_kanban import api_server as api_server
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_rem_kanban import wait_for


async def final(**options: Any) -> Any:
    yield {"type": "final", "message": {"body": "Done."}}


def organization(tmp_path: Path) -> tuple[OrganizationManager, dict[str, Any], str]:
    manager = OrganizationManager(tmp_path / "org-data", stream=final, start_runtime=False)
    root = tmp_path / "org-project"
    root.mkdir()
    org = manager.call(
        str(root),
        "user",
        {
            "action": "create",
            "params": {
                "config": {
                    "name": "Desk",
                    "employees": [{"id": "lead", "name": "Avery", "permissionMode": "read-only"}],
                }
            },
        },
    )
    config = copy.deepcopy(manager.store.get("", org["id"])["config"])
    config.pop("packageFiles", None)
    config["employees"][0]["permissionMode"] = "workspace-write"
    asked = manager.call(
        str(root),
        "Rem:thread",
        {
            "action": "configure",
            "organizationId": org["id"],
            "params": {"config": config, "expectedRevision": org["revision"], "reason": "x"},
        },
    )
    assert asked["needsUserConsent"] is True
    return manager, org, asked["requestId"]


def reply_to(home: Any, request: str, body: str) -> str:
    home.submit_message({"request_id": request, "body": body})
    home.tick()
    wait_for(
        lambda: any(m["id"] == f"reply-{request}" for m in home.store.conversation()["messages"])
    )
    return str(
        next(
            m["body"]
            for m in home.store.conversation()["messages"]
            if m["id"] == f"reply-{request}"
        )
    )


def test_the_users_message_approves_and_the_reply_says_what(board, tmp_path):
    home, _, _ = board
    manager, org, rid = organization(tmp_path)
    home.organizations = manager
    prompts: list[str] = []

    def generate(prompt: str, config: Any, cancel: Any) -> Any:
        prompts.append(prompt)
        return {
            "reply": "Approving them now.",
            "tasks": [],
            "memories": [],
            "org_approvals": [{"organization_id": org["id"], "all": True}],
        }

    home.generate = generate
    try:
        reply = reply_to(home, "say-so", "Approve those for me")
        request = manager.store.get("", org["id"])["runtime"]["consentRequests"][rid]
        assert request["status"] == "approved"
        assert request["decidedBy"] == "Rem, on your instruction"
        assert request["instruction"]["messageId"] == "say-so"
        assert "On your instruction, in Desk:" in reply and "- Approved:" in reply
        # The chat sees what is waiting, with exact IDs.
        assert rid in prompts[0] and "org_approvals" in prompts[0]
    finally:
        manager.close()


def test_an_implied_okay_approves_on_rems_judgment(board, tmp_path):
    # No magic words: Rem judged "Yep, do it" an obvious okay, so it counts, and Rem's
    # reason is kept with the approval.
    home, _, _ = board
    manager, org, rid = organization(tmp_path)
    home.organizations = manager
    home.generate = lambda *args: {
        "reply": "Going ahead.",
        "tasks": [],
        "memories": [],
        "org_approvals": [
            {
                "organization_id": org["id"],
                "request_ids": [rid],
                "reason": "Answered my question about the permission change",
            }
        ],
    }
    try:
        reply = reply_to(home, "implied", "Yep, do it")
        request = manager.store.get("", org["id"])["runtime"]["consentRequests"][rid]
        assert request["status"] == "approved"
        assert request["instruction"]["messageId"] == "implied"
        assert request["instruction"]["reason"] == (
            "Answered my question about the permission change"
        )
        assert "- Approved:" in reply
    finally:
        manager.close()


def test_rem_leaving_org_approvals_empty_approves_nothing(board, tmp_path):
    home, _, _ = board
    manager, org, rid = organization(tmp_path)
    home.organizations = manager
    home.generate = lambda *args: {
        "reply": "Here's what is waiting.",
        "tasks": [],
        "memories": [],
        "org_approvals": [],
    }
    try:
        reply_to(home, "ask", "What is waiting in the desk?")
        request = manager.store.get("", org["id"])["runtime"]["consentRequests"][rid]
        assert request["status"] == "pending"
    finally:
        manager.close()


def test_a_workflow_prompt_cannot_approve_even_with_actions_allowed(board, tmp_path):
    # Only the user's own message counts: a workflow asking Rem (whose text an outside
    # trigger may have written) never approves, however obvious its wording.
    from gofer.ui.rem_workflow import WorkflowOrigin, submit_prompt

    home, _, _ = board
    manager, org, rid = organization(tmp_path)
    home.organizations = manager
    home.generate = lambda *args: {
        "reply": "Approving.",
        "tasks": [],
        "memories": [],
        "org_approvals": [{"organization_id": org["id"], "all": True, "reason": "said so"}],
    }
    try:
        identity = submit_prompt(
            home.store,
            WorkflowOrigin("wf", "run-1", "ask"),
            "Approve everything waiting in the Desk",
            allow_actions=True,
        )
        home.tick()
        wait_for(
            lambda: any(
                m["id"] == f"reply-{identity}" for m in home.store.conversation()["messages"]
            )
        )
        request = manager.store.get("", org["id"])["runtime"]["consentRequests"][rid]
        assert request["status"] == "pending"
    finally:
        manager.close()


def test_workflows_and_screens_cannot_approve():
    from gofer.ui.rem_home import workflow_answer_only
    from gofer.ui.rem_home_models import Decision
    from gofer.ui.rem_screen import acts

    decision = Decision.model_validate(
        {"reply": "Approved.", "org_approvals": [{"organization_id": "o", "all": True}]}
    )
    # Counts as acting, so screen-shared and workflow turns drop it.
    assert acts(decision)
    assert workflow_answer_only(decision).org_approvals == []
