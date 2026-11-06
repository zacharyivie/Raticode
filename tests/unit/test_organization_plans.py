"""Plans, tickets, shared knowledge, typed links and evidence-checked claims."""

from __future__ import annotations

import gzip
import json
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.organization_knowledge import run_check
from gofer.ui.organization_store import OrganizationConflict
from gofer.ui.organizations import OrganizationManager


async def final(**options: Any) -> Any:
    yield {"type": "final", "message": {"body": "Done."}}


def start(tmp_path: Path, stream: Any = final) -> OrganizationManager:
    return OrganizationManager(tmp_path / "data", stream=stream, start_runtime=False)


def company(manager: OrganizationManager, root: Path, **config: Any) -> Any:
    employees = config.pop(
        "employees",
        [
            {"id": "lead", "name": "Avery Lane"},
            {"id": "builder", "name": "Mira Santos", "reportsTo": "lead"},
        ],
    )
    return manager.call(
        str(root),
        "user",
        {
            "action": "create",
            "params": {
                "config": {
                    "name": "Raticode Studio",
                    "slug": "studio",
                    "employees": employees,
                    **config,
                }
            },
        },
    )


def act(
    manager: OrganizationManager,
    org: dict[str, Any],
    action: str,
    actor: str = "user",
    **params: Any,
) -> Any:
    return manager.call(
        org["projectRoot"], actor, {"organizationId": org["id"], "action": action, "params": params}
    )


def read(manager: OrganizationManager, org: dict[str, Any]) -> Any:
    return act(manager, org, "read")


def save(
    manager: OrganizationManager, org: dict[str, Any], kind: str, actor: str = "user", **item: Any
) -> Any:
    revision = read(manager, org)["revision"]
    return act(manager, org, "plan_save", actor, kind=kind, item=item, expectedRevision=revision)


def planned(manager: OrganizationManager, org: dict[str, Any], auto: bool = True) -> Any:
    save(manager, org, "mission", mission="Make agent work something you can trust at a glance.")
    save(
        manager,
        org,
        "project",
        id="desktop",
        name="Raticode Desktop",
        owner="lead",
        checks=[
            {"name": "unit", "command": "echo 3 passed"},
            {"name": "broken", "command": "echo FAILED >&2; exit 1"},
        ],
        autoRunChecks=auto,
    )
    save(manager, org, "project", id="docs", name="Docs site")
    save(
        manager,
        org,
        "milestone",
        id="M1",
        title="Every agent claim has evidence",
        dueAt="2027-01-31",
    )
    save(
        manager,
        org,
        "milestone",
        id="PM1",
        title="Organizations 2.0",
        projectId="desktop",
        parentId="M1",
        dueAt="2027-01-15",
    )
    save(
        manager, org, "epic", id="E1", title="Evidence receipts", milestoneId="PM1", owner="builder"
    )
    return read(manager, org)


def settle(manager: OrganizationManager) -> None:
    until = time.monotonic() + 10
    while manager._active and time.monotonic() < until:
        time.sleep(0.02)
    assert not manager._active


def wait_for(condition: Any, seconds: float = 15) -> None:
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError("Timed out")


def test_plans_are_audited_revisions_with_consistent_references(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    state = planned(manager, org)
    config = state["config"]
    assert config["mission"].startswith("Make agent work")
    assert [p["id"] for p in config["projects"]] == ["desktop", "docs"]
    assert config["epics"][0]["projectId"] == "desktop"  # Derived from its milestone.
    reasons = [entry["reason"] for entry in act(manager, org, "history")]
    assert "Saved epic Evidence receipts" in reasons and "Updated the mission" in reasons
    with pytest.raises(ValueError, match="project milestone"):
        save(manager, org, "epic", title="Loose", milestoneId="M1")
    with pytest.raises(ValueError, match="Only project milestones"):
        save(manager, org, "milestone", title="Nested", parentId="M1")
    with pytest.raises(ValueError, match="serves an unknown"):
        save(manager, org, "milestone", title="Bad", projectId="docs", parentId="PM1")
    with pytest.raises(ValueError, match="unknown owner"):
        save(manager, org, "epic", title="Orphan", milestoneId="PM1", owner="ghost")
    # Stale revisions are refused like any configuration change.
    with pytest.raises(OrganizationConflict):
        act(manager, org, "plan_save", kind="mission", item={"mission": "x"}, expectedRevision=1)
    manager.close()


def test_plan_items_in_use_cannot_be_deleted(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    state = planned(manager, org)
    act(manager, org, "task_create", title="Claim records", epicId="E1", assignee="builder")
    revision = read(manager, org)["revision"]
    with pytest.raises(ValueError, match="1 tickets"):
        act(manager, org, "plan_delete", kind="epic", id="E1", expectedRevision=revision)
    with pytest.raises(ValueError, match="epics first"):
        act(manager, org, "plan_delete", kind="milestone", id="PM1", expectedRevision=revision)
    with pytest.raises(ValueError, match="still serve"):
        act(manager, org, "plan_delete", kind="milestone", id="M1", expectedRevision=revision)
    with pytest.raises(ValueError, match="milestones and epics first"):
        act(manager, org, "plan_delete", kind="project", id="desktop", expectedRevision=revision)
    result = act(manager, org, "plan_delete", kind="project", id="docs", expectedRevision=revision)
    assert [p["id"] for p in result["config"]["projects"]] == ["desktop"]
    assert state["revision"] < result["revision"]
    manager.close()


def test_tickets_get_keys_and_follow_their_epic_project(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    planned(manager, org)
    first = act(
        manager,
        org,
        "task_create",
        title="Claim records",
        epicId="E1",
        doneWhen="A run can post at most five claims",
    )
    second = act(manager, org, "task_create", title="Flag stale claims", epicId="E1")
    assert (first["number"], second["number"]) == (1, 2)
    assert first["project"] == "desktop"
    tasks = read(manager, org)["runtime"]["tasks"]
    assert [t["key"] for t in tasks] == ["STU-1", "STU-2"]
    with pytest.raises(ValueError, match="epic's project"):
        act(manager, org, "task_create", title="Wrong", epicId="E1", project="docs")
    with pytest.raises(ValueError, match="Unknown epic"):
        act(manager, org, "task_create", title="Lost", epicId="E9")
    # Ticket keys work wherever a ticket is named.
    assert act(manager, org, "ticket_changes", taskId="STU-2")["branch"] is None
    manager.close()


def test_tasks_saved_before_ticket_numbers_keep_stable_numbers(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    for title in ("Older", "Old"):
        act(manager, org, "task_create", title=title)
    with sqlite3.connect(manager.store.path) as db:
        for tid, body in db.execute(
            "SELECT id, body FROM organization_tasks WHERE organization=?", (org["id"],)
        ).fetchall():
            data = json.loads(body)
            data.pop("number")
            db.execute("UPDATE organization_tasks SET body=? WHERE id=?", (json.dumps(data), tid))
    numbers = [t["number"] for t in read(manager, org)["runtime"]["tasks"]]
    assert numbers == [1, 2]
    created = act(manager, org, "task_create", title="New")
    assert created["number"] == 3
    assert [t["number"] for t in read(manager, org)["runtime"]["tasks"]] == [1, 2, 3]
    manager.close()


def test_worker_persona_and_responsibilities_are_validated(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(
        manager,
        tmp_path,
        employees=[
            {
                "id": "sage",
                "name": "Sage Kim",
                "persona": "Reads the source before the summary.",
                "responsibilities": ["Cite sources", "  ", "Keep knowledge fresh"],
            }
        ],
    )
    employee = org["config"]["employees"][0]
    assert employee["responsibilities"] == ["Cite sources", "Keep knowledge fresh"]
    config = {**org["config"]}
    config["employees"] = [{**employee, "responsibilities": ["x" * 201]}]
    with pytest.raises(ValueError, match="200 characters"):
        act(
            manager,
            org,
            "configure",
            config=config,
            expectedRevision=org["revision"],
            reason="Too long",
        )
    manager.close()


def test_rem_needs_consent_to_add_commands_raticode_runs(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    request = save(
        manager,
        org,
        "project",
        "Rem:thread",
        name="Desktop",
        checks=[{"name": "unit", "command": "pytest -q"}],
    )
    assert request["needsUserConsent"] is True
    assert "commands Raticode runs" in request["summary"]
    assert read(manager, org)["config"]["projects"] == []
    act(manager, org, "request_decide", requestId=request["requestId"], decision="approve")
    assert read(manager, org)["config"]["projects"][0]["checks"][0]["command"] == "pytest -q"
    # Plain plan changes from Rem apply directly.
    applied = save(manager, org, "milestone", "Rem:thread", title="Phone control")
    assert applied["config"]["milestones"][0]["title"] == "Phone control"
    manager.close()


def test_knowledge_entries_are_versioned_and_searchable(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    planned(manager, org)
    first = act(
        manager,
        org,
        "knowledge_save",
        entry={
            "kind": "decision",
            "title": "The board has five fixed columns",
            "topic": "Board",
            "projectId": "desktop",
            "body": "Backlog, To do, Running, Review, Done.\n\nOnly you move work into Done.",
        },
    )
    assert first["version"] == 1 and first["summary"].startswith("Backlog")
    second = act(
        manager,
        org,
        "knowledge_save",
        entry={
            "kind": "spec",
            "title": "Card anatomy",
            "body": "Key, title, owner.",
            "links": [first["id"]],
        },
    )
    with pytest.raises(OrganizationConflict):
        act(
            manager,
            org,
            "knowledge_save",
            entry={"id": first["id"], "title": "x"},
            expectedVersion=0,
        )
    updated = act(
        manager,
        org,
        "knowledge_save",
        entry={"id": first["id"], "topic": "Planning"},
        expectedVersion=1,
    )
    assert updated["version"] == 2 and len(updated["history"]) == 2
    with pytest.raises(ValueError, match="existing knowledge"):
        act(manager, org, "knowledge_save", entry={"title": "Bad", "links": ["k-nope"]})
    found = act(manager, org, "knowledge_search", query="columns")["items"]
    assert [item["id"] for item in found] == [first["id"]]
    assert second["links"] == [first["id"]]
    events = [e["kind"] for e in act(manager, org, "events")]
    assert events.count("knowledge_save") == 3
    assert act(manager, org, "audit_verify")["ok"] is True
    manager.close()


def test_second_brain_notes_are_added_refreshed_and_mirrored(tmp_path: Path) -> None:
    brain = tmp_path / "brain"
    (brain / "Research").mkdir(parents=True)
    note = brain / "Research" / "logging.md"
    note.write_text("# Logs never include tokens\n\nDevice names are fine. Tokens never are.\n")
    outside = tmp_path / "secret.md"
    outside.write_text("# Private\n")
    manager = start(tmp_path)
    org = company(manager, tmp_path, remSecondBrain={"enabled": True, "root": str(brain)})
    entry = act(manager, org, "knowledge_import", path="Research/logging.md", kind="decision")
    assert entry["title"] == "Logs never include tokens"
    assert entry["source"]["path"] == str(note.resolve())
    with pytest.raises(ValueError, match="inside the organization's Second Brain"):
        act(manager, org, "knowledge_import", path=str(outside))
    with pytest.raises(ValueError, match="Edit Second Brain notes"):
        act(
            manager,
            org,
            "knowledge_save",
            entry={"id": entry["id"], "body": "x"},
            expectedVersion=1,
        )
    note.write_text("# Logs never include tokens\n\nDevice names and timings are fine.\n")
    refreshed = act(manager, org, "knowledge_refresh")
    assert refreshed["changed"] == [entry["id"]]
    current = read(manager, org)["runtime"]["knowledge"][entry["id"]]
    assert current["version"] == 2 and current["changedAt"]
    # Mirroring into the Second Brain is off until the user turns it on.
    written = act(manager, org, "knowledge_save", entry={"title": "Guide style", "body": "Short."})
    assert "mirror" not in written
    config = {**read(manager, org)["config"], "knowledgeToSecondBrain": True}
    request = act(
        manager,
        org,
        "configure",
        "Rem:thread",
        config=config,
        expectedRevision=read(manager, org)["revision"],
        reason="Mirror",
    )
    assert "Second Brain folder" in request["summary"]
    act(manager, org, "request_decide", requestId=request["requestId"], decision="approve")
    mirrored = act(
        manager,
        org,
        "knowledge_save",
        entry={"id": written["id"], "body": "Shorter."},
        expectedVersion=1,
    )
    path = Path(mirrored["mirror"]["path"])
    assert path.is_relative_to(brain / "Organizations" / "studio")
    assert "Shorter." in path.read_text()
    manager.close()


def _repo(path: Path) -> Path:
    path.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x",
        "PATH": "/usr/bin:/bin",
    }
    for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, env=env)
    return path


def test_a_run_reads_knowledge_posts_claims_and_raticode_checks_them(tmp_path: Path) -> None:
    root = _repo(tmp_path / "project")
    seen: dict[str, Any] = {}
    holder: dict[str, OrganizationManager] = {}

    async def employee(**options: Any) -> Any:
        manager = holder["manager"]
        run = options["workflow"]["organizationRun"]
        state = manager.store.get("", run["organizationId"])
        context = {
            "employee_id": "builder",
            "bound_org": run["organizationId"],
            "generation": state["runtime"]["generation"],
            "run_id": run["parentRunId"],
        }

        def tool(action: str, **params: Any) -> Any:
            return manager.call(
                str(root),
                "employee:builder",
                {"action": action, "organizationId": run["organizationId"], "params": params},
                **context,
            )

        seen["input"] = json.loads(options["messages"][0]["body"])
        seen["instructions"] = options["agent_instructions"]
        note = tool("knowledge_search", query="columns")["items"][0]
        tool("knowledge_read", id=note["id"])
        tool("knowledge_read", id=note["id"])  # Read twice, recorded once.
        work = Path(options["working_dir"])
        (work / "board.js").write_text("export const columns = 5;\n")
        env = {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@x",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@x",
            "PATH": "/usr/bin:/bin",
        }
        subprocess.run(["git", "-C", str(work), "add", "."], check=True, env=env)
        subprocess.run(["git", "-C", str(work), "commit", "-qm", "Board"], check=True, env=env)
        sha = subprocess.run(
            ["git", "-C", str(work), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        key = seen["input"]["plan"]["ticket"]
        tool(
            "claim_add",
            taskId=key,
            text="Unit tests pass",
            evidence={"kind": "check", "command": "unit", "reported": "passed"},
        )
        tool(
            "claim_add",
            taskId=key,
            text="The failing check passes",
            evidence={"kind": "check", "command": "echo FAILED >&2; exit 1"},
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
            text="Committed the board",
            evidence={"kind": "commit", "sha": sha},
        )
        tool(
            "claim_add",
            taskId=key,
            text="Ran my own script",
            evidence={"kind": "check", "command": "touch ran-by-user"},
        )
        try:
            tool("claim_add", taskId=key, text="One too many", evidence=None)
        except ValueError as exc:
            seen["limit"] = str(exc)
        try:
            tool("check_run", claimId="c-any")
        except ValueError as exc:
            seen["check_run"] = str(exc)
        try:
            tool("plan_save", kind="mission", item={"mission": "x"}, expectedRevision=1)
        except ValueError as exc:
            seen["plan"] = str(exc)
        yield {"type": "final", "message": {"body": "Done."}}

    manager = start(tmp_path, employee)
    holder["manager"] = manager
    org = company(manager, root)
    planned(manager, org)
    entry = act(
        manager,
        org,
        "knowledge_save",
        entry={
            "kind": "decision",
            "title": "The board has five fixed columns",
            "body": "Backlog, To do, Running, Review, Done. Only you move work into Done.",
        },
    )
    task = act(
        manager,
        org,
        "task_create",
        title="Drag cards",
        epicId="E1",
        assignee="builder",
        doneWhen="Cards move by drag or keyboard",
        knowledge=[entry["id"]],
    )
    act(manager, org, "control", state="running")
    manager.dispatch()
    settle(manager)

    plan = seen["input"]["plan"]
    assert plan["ticket"] == "STU-1" and plan["epic"]["title"] == "Evidence receipts"
    assert plan["milestone"]["title"] == "Organizations 2.0"
    assert plan["servesOrganizationMilestone"] == "Every agent claim has evidence"
    assert plan["linkedKnowledge"][0]["id"] == entry["id"]
    assert plan["projectChecks"][0] == "unit: echo 3 passed"
    assert "Organization mission: Make agent work" in seen["instructions"]
    assert "claim_add" in seen["instructions"]
    assert "at most 5" in seen["limit"]
    assert "Only the user or Rem" in seen["check_run"] and "Only the user or Rem" in seen["plan"]

    state = read(manager, org)
    reads = [link for link in state["runtime"]["links"].values() if link["kind"] == "read"]
    assert len(reads) == 1
    assert reads[0]["from"] == f"ticket:{task['id']}" and reads[0]["to"] == f"kb:{entry['id']}"

    def states() -> dict[str, str]:
        claims = read(manager, org)["runtime"]["claims"].values()
        return {c["text"]: c["evidence"]["state"] for c in claims}

    wait_for(
        lambda: (
            states()["Unit tests pass"] == "ok" and states()["The failing check passes"] == "bad"
        )
    )
    claims = {c["text"]: c for c in read(manager, org)["runtime"]["claims"].values()}
    unit = claims["Unit tests pass"]["evidence"]
    assert unit["exitCode"] == 0 and "3 passed" in unit["output"] and unit["ranBy"] == "Raticode"
    worktree = read(manager, org)["runtime"]["tasks"][0]["worktree"]
    assert unit["headCommit"] and worktree["root"] != str(root)
    assert claims["The failing check passes"]["evidence"]["command"] == "echo FAILED >&2; exit 1"
    assert "FAILED" in claims["The failing check passes"]["evidence"]["output"]
    assert claims["Follows the five-column decision"]["evidence"]["state"] == "ok"
    commit = claims["Committed the board"]["evidence"]
    assert commit["state"] == "ok" and commit["files"] == [["board.js", 1, 0]]
    # Commands that aren't project checks never run on their own.
    own = claims["Ran my own script"]
    assert own["evidence"]["state"] == "unverified"
    assert not (Path(worktree["root"]) / "ran-by-user").exists()
    assert run_check(manager, "", org["id"], own["id"], "user")["exitCode"] == 0
    assert (Path(worktree["root"]) / "ran-by-user").exists()

    changes = act(manager, org, "ticket_changes", taskId=task["id"])
    assert changes["branch"] == worktree["branch"]
    assert changes["files"] == [["board.js", 1, 0]]
    assert changes["commits"][0][1] == "Board"

    # Editing the quoted note flags the claim instead of hiding the change.
    act(
        manager,
        org,
        "knowledge_save",
        entry={"id": entry["id"], "body": "Backlog, To do, Running, Review, Done."},
        expectedVersion=1,
    )
    claims = {c["text"]: c for c in read(manager, org)["runtime"]["claims"].values()}
    assert claims["Follows the five-column decision"]["evidence"]["state"] == "warn"

    # Requesting changes clears the claims for the next run but keeps the records.
    current = next(t for t in read(manager, org)["runtime"]["tasks"] if t["id"] == task["id"])
    assert current["status"] == "in_review"
    act(
        manager,
        org,
        "review",
        taskId=task["id"],
        expectedRevision=current["revision"],
        decision="revise",
        body="Fix the failing check",
    )
    claims = read(manager, org)["runtime"]["claims"].values()
    assert len(claims) == 5 and all(c["supersededAt"] for c in claims)

    attempt = next(iter(read(manager, org)["runtime"]["attempts"].values()))
    stored = json.loads(gzip.open(manager.run_input_path(org["id"], attempt["id"])).read())
    assert "Every agent claim has evidence" in stored["messages"][0]["body"]
    assert act(manager, org, "audit_verify")["ok"] is True
    manager.close()


def test_agent_links_need_an_exact_quote(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    a = act(
        manager,
        org,
        "knowledge_save",
        entry={
            "title": "Observed and asserted links",
            "body": "Asserted links need a checkable quote.",
        },
    )
    b = act(manager, org, "knowledge_save", entry={"title": "Claims", "body": "Five per run."})
    with pytest.raises(ValueError, match="isn't in either entry"):
        act(
            manager,
            org,
            "knowledge_link",
            **{"from": a["id"], "to": b["id"], "quote": "made up words here"},
        )
    link = act(
        manager,
        org,
        "knowledge_link",
        **{"from": a["id"], "to": b["id"], "quote": "need a checkable quote"},
    )
    assert link["quotedFrom"] == a["id"] and link["origin"] == "user"
    again = act(
        manager, org, "knowledge_link", **{"from": b["id"], "to": a["id"], "quote": "Five per run"}
    )
    assert again["id"] == link["id"]  # One link per pair.
    links = read(manager, org)["runtime"]["links"]
    assert links[link["id"]]["state"] == "ok"
    act(
        manager,
        org,
        "knowledge_save",
        entry={"id": a["id"], "body": "Rewritten."},
        expectedVersion=1,
    )
    assert read(manager, org)["runtime"]["links"][link["id"]]["state"] == "warn"
    removed = act(manager, org, "knowledge_link", linkId=link["id"], remove=True)
    assert removed["removed"]["actor"] == "user"
    manager.close()


def test_packages_carry_plans_and_personas_but_never_check_commands(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(
        manager,
        tmp_path,
        employees=[
            {
                "id": "lead",
                "name": "Avery Lane",
                "persona": "Calm planner.",
                "responsibilities": ["Plan milestones"],
            },
            {"id": "builder", "name": "Mira Santos"},
        ],
    )
    planned(manager, org)
    act(manager, org, "task_create", title="Claim records", epicId="E1", doneWhen="Five claims")
    exported = act(manager, org, "export")
    other = tmp_path / "other"
    other.mkdir()
    imported = manager.call(
        str(other), "user", {"action": "import", "params": {"zip": exported["zip"]}}
    )
    config = imported["config"]
    assert config["mission"].startswith("Make agent work")
    assert [m["id"] for m in config["milestones"]] == ["M1", "PM1"]
    assert config["epics"][0]["title"] == "Evidence receipts"
    assert all("checks" not in p for p in config["projects"])
    lead = next(e for e in config["employees"] if e["id"] == "lead")
    assert lead["persona"] == "Calm planner." and lead["responsibilities"] == ["Plan milestones"]
    task = imported["runtime"]["tasks"][0]
    assert task["epicId"] == "E1" and task["doneWhen"] == "Five claims"
    manager.close()


def test_recent_activity_skips_run_transcripts(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    for title in ("One", "Two", "Three"):
        act(manager, org, "task_create", title=title)
    with manager.store.connect() as db:
        manager.store.event(db, org["id"], "system", "runtime_recovered", {"reason": "restart"})
    recent = act(manager, org, "events", recent=2)
    assert [e["kind"] for e in recent] == ["task_create", "task_create"]
    assert recent[0]["sequence"] < recent[1]["sequence"]
    manager.close()


def test_accepting_work_records_its_claims_as_evidence(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    task = act(manager, org, "task_create", title="Explain", assignee="lead")
    act(manager, org, "control", state="running")
    manager.dispatch()
    settle(manager)
    act(manager, org, "claim_add", taskId=task["id"], text="Explained the change")
    current = read(manager, org)["runtime"]["tasks"][0]
    assert current["status"] == "in_review" and not current["evidence"]
    act(
        manager,
        org,
        "review",
        taskId=task["id"],
        expectedRevision=current["revision"],
        decision="accept",
        body="Looks right",
    )
    done = read(manager, org)["runtime"]["tasks"][0]
    assert done["status"] == "done"
    assert (
        done["evidence"]
        == "Accepted after review: Looks right\n- Explained the change (no evidence)"
    )
    manager.close()


def test_imported_notes_summarize_their_first_paragraph(tmp_path: Path) -> None:
    brain = tmp_path / "brain"
    brain.mkdir()
    (brain / "note.md").write_text("---\ntags: x\n---\n# Title\n\nThe first real paragraph.\n")
    manager = start(tmp_path)
    org = company(manager, tmp_path, remSecondBrain={"enabled": True, "root": str(brain)})
    entry = act(manager, org, "knowledge_import", path="note.md")
    assert (entry["title"], entry["summary"]) == ("Title", "The first real paragraph.")
    manager.close()


def test_finished_tickets_keep_their_branch_after_the_worktree_is_removed(tmp_path: Path) -> None:
    root = _repo(tmp_path / "project")

    async def builder(**options: Any) -> Any:
        work = Path(options["working_dir"])
        (work / "notes.txt").write_text("done\n")
        env = {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@x",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@x",
            "PATH": "/usr/bin:/bin",
        }
        subprocess.run(["git", "-C", str(work), "add", "."], check=True, env=env)
        subprocess.run(["git", "-C", str(work), "commit", "-qm", "Notes"], check=True, env=env)
        yield {"type": "final", "message": {"body": "Done."}}

    manager = start(tmp_path, builder)
    org = company(manager, root)
    task = act(manager, org, "task_create", title="Write notes", assignee="lead")
    act(manager, org, "control", state="running")
    manager.dispatch()
    settle(manager)
    current = read(manager, org)["runtime"]["tasks"][0]
    act(
        manager,
        org,
        "review",
        taskId=task["id"],
        expectedRevision=current["revision"],
        decision="accept",
        body="Good",
    )
    manager.release_worktrees(manager.store.get("", org["id"]))
    finished = read(manager, org)["runtime"]["tasks"][0]
    assert finished["worktree"] is None and finished["releasedWorktree"]["branch"]
    changes = act(manager, org, "ticket_changes", taskId=task["id"])
    assert changes["branch"] == finished["releasedWorktree"]["branch"]
    assert changes["files"] == [["notes.txt", 1, 0]]
    manager.close()


def test_project_checks_wait_for_the_user_unless_the_project_opts_in(tmp_path: Path) -> None:
    root = _repo(tmp_path / "project")
    holder: dict[str, OrganizationManager] = {}

    async def employee(**options: Any) -> Any:
        manager = holder["manager"]
        run = options["workflow"]["organizationRun"]
        state = manager.store.get("", run["organizationId"])
        manager.call(
            str(root),
            "employee:builder",
            {
                "action": "claim_add",
                "organizationId": run["organizationId"],
                "params": {
                    "taskId": run["taskId"],
                    "text": "Unit tests pass",
                    "evidence": {"kind": "check", "command": "touch ran.txt; echo 3 passed"},
                },
            },
            employee_id="builder",
            bound_org=run["organizationId"],
            generation=state["runtime"]["generation"],
            run_id=run["parentRunId"],
        )
        yield {"type": "final", "message": {"body": "Done."}}

    manager = start(tmp_path, employee)
    holder["manager"] = manager
    org = company(manager, root)
    planned(manager, org, auto=False)
    revision = read(manager, org)["revision"]
    act(
        manager,
        org,
        "plan_save",
        kind="project",
        expectedRevision=revision,
        item={
            "id": "desktop",
            "checks": [{"name": "unit", "command": "touch ran.txt; echo 3 passed"}],
        },
    )
    task = act(manager, org, "task_create", title="Board", epicId="E1", assignee="builder")
    act(manager, org, "control", state="running")
    manager.dispatch()
    settle(manager)
    time.sleep(0.3)
    claim = next(iter(read(manager, org)["runtime"]["claims"].values()))
    worktree = Path(read(manager, org)["runtime"]["tasks"][0]["worktree"]["root"])
    # Nothing ran: the check would execute the branch's code as the user.
    assert claim["evidence"]["state"] == "queued" and not (worktree / "ran.txt").exists()
    with pytest.raises(ValueError, match="Only you"):
        act(manager, org, "check_run", "Rem:thread", taskId=task["id"])
    assert act(manager, org, "check_run", taskId=task["id"]) == {"started": 1, "taskId": task["id"]}

    def state() -> str:
        return str(
            next(iter(read(manager, org)["runtime"]["claims"].values()))["evidence"]["state"]
        )

    wait_for(lambda: state() == "ok")
    checked = next(iter(read(manager, org)["runtime"]["claims"].values()))["evidence"]
    assert checked["ranBy"] == "you" and (worktree / "ran.txt").exists()
    with pytest.raises(ValueError, match="no checks waiting"):
        act(manager, org, "check_run", taskId=task["id"])
    # Turning on automatic reruns from Rem needs the user's consent.
    request = act(
        manager,
        org,
        "plan_save",
        "Rem:thread",
        kind="project",
        expectedRevision=read(manager, org)["revision"],
        item={"id": "desktop", "autoRunChecks": True},
    )
    assert "automatically" in request["summary"]
    manager.close()
