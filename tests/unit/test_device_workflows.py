"""Workflows on a paired phone: scoped views, desktop-owned runs and kept safeguards."""

import json
import threading
import time
from pathlib import Path
from typing import Any

import anyio
import pytest

import gofer.ui.api as api
from gofer.devices import workflows as module
from gofer.rattish.editor import source_revision
from gofer.rattish.run_service import resume_rattish_run, run_rattish_file
from gofer.rattish.runtime import HandlerResult, NodeHandlerRegistry
from gofer.rattish.workspaces import create_registered_workflow
from tests.unit.test_device_application import application as application
from tests.unit.test_device_application import event
from tests.unit.test_device_desktop_control import phone as phone

SOURCE = (
    "Rattish: 1\nWorkflow:\n  name: Phone check\n"
    "Node build:\n  type: bash-command\n  command: fake-only\n  to: report\n"
    "Node report:\n  type: bash-command\n  command: fake-only\n  needs: build\n  to: lint\n"
    "Node lint:\n  type: bash-command\n  command: fake-only\n"
    "  needs:\n    - build\n    - report\n"
)


@pytest.fixture
def desk(phone, tmp_path, monkeypatch):
    """A phone allowed to use the desktop, with isolated workflows and a fake runner."""
    app, peer, context, send = phone
    base = tmp_path / "workflow-data"
    app.remote.data_dir = base
    app.desktop.enable(peer, True)
    release = threading.Event()
    ran: list[str] = []

    async def handler(node, run_context, bindings):
        ran.append(node["id"])
        while not release.is_set():
            await anyio.sleep(0.01)
        return HandlerResult(True, {"stdout": "done", "stderr": "", "exit_code": 0})

    async def fake_run(*args, **kwargs):
        return await run_rattish_file(
            *args, **kwargs, handlers=NodeHandlerRegistry({"raticode.bash_command": handler})
        )

    async def fake_resume(*args, **kwargs):
        return await resume_rattish_run(
            *args, **kwargs, handlers=NodeHandlerRegistry({"raticode.bash_command": handler})
        )

    monkeypatch.setattr(api, "run_rattish_file", fake_run)
    monkeypatch.setattr(api, "resume_rattish_run", fake_resume)

    def add(root: str | Path, name: str, source: str = SOURCE) -> Any:
        workflow = create_registered_workflow(Path(root), name, registry_dir=base)
        workflow.entrypoint.write_text(source.replace("Phone check", name), encoding="utf-8")
        return workflow

    yield app, peer, context, send, add, release, ran, base
    release.set()
    deadline = time.monotonic() + 5
    with api._active_run_lock:
        pending = bool(api._active_rattish_runs)
    while pending and time.monotonic() < deadline:
        time.sleep(0.02)
        with api._active_run_lock:
            pending = bool(api._active_rattish_runs)


def plain(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "digest"}


def wait_for(read, done, timeout: float = 5) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        value: dict[str, Any] = read()
        if done(value) or time.monotonic() > deadline:
            return value
        time.sleep(0.02)


def test_workflows_need_phone_access_and_are_advertised(phone, tmp_path):
    app, peer, _, send = phone
    assert send("desktop.read", "workflows.list")["code"] == "unauthorized"
    assert send("desktop.action", "workflows.run", {"workflow_id": "x"})["code"] == "unauthorized"
    app.desktop.enable(peer, True)
    assert send("desktop.read", "overview")["value"]["workflows"] is False
    assert send("desktop.read", "workflows.list")["code"] == "unavailable"
    app.remote.data_dir = tmp_path / "data"
    assert send("desktop.read", "overview")["value"]["workflows"] is True
    assert plain(send("desktop.read", "workflows.list")["value"]) == {
        "workflows": [],
        "earlier": 0,
    }


def test_views_show_open_project_workflows_without_paths(desk, tmp_path):
    app, peer, context, send, add, _, _, base = desk
    root = context["projects"][0]["root"]
    shown = add(root, "Phone check")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    hidden = add(elsewhere, "Hidden")

    listed = send("desktop.read", "workflows.list")["value"]
    assert [w["workflow_id"] for w in listed["workflows"]] == [shown.workflow_id]
    entry = listed["workflows"][0]
    assert entry["name"] == "Phone check" and entry["project_name"] == "Notes"
    assert entry["status"] == "idle" and entry["latest_run"] is None
    assert entry["description"] == "3 nodes, 2 routes."

    view = send("desktop.read", "workflows.read", {"workflow_id": shown.workflow_id})["value"]
    text = json.dumps(view)
    assert root not in text and str(base) not in text and "fake-only" not in text
    assert view["revision"] == source_revision(shown.entrypoint.read_text())
    assert [n["node_id"] for n in view["nodes"]] == ["build", "lint", "report"]
    assert {(e["from"], e["to"], e["label"]) for e in view["edges"]} == {
        ("build", "report", ""),
        ("report", "lint", ""),
        ("build", "lint", "needs"),
    }
    assert view["runs"] == [] and view["run"] is None and view["earlier_runs"] == 0
    assert view["preview"]["blocking"] == []
    assert any(item.startswith("build:") for item in view["preview"]["destructive"])
    assert view["controls"]["run"] == {"allowed": True, "reason": ""}
    assert view["controls"]["stop"] == {"allowed": False, "reason": module.NOT_RUNNING}
    assert view["controls"]["resume"] == {"allowed": False, "reason": module.NO_RUN}

    # Workflows outside the phone's open projects can't be read or run.
    refused = send("desktop.read", "workflows.read", {"workflow_id": hidden.workflow_id})
    assert refused == {"ok": False, "code": "not_found", "detail": module.NOT_OPEN}
    revision = source_revision(hidden.entrypoint.read_text())
    run = send(
        "desktop.action",
        "workflows.run",
        {"workflow_id": hidden.workflow_id, "expected_revision": revision},
    )
    assert run["code"] == "not_found"
    assert send("desktop.read", "workflows.read", {"workflow_id": "gone"})["code"] == "not_found"


def test_run_status_and_stop_use_the_desktop_runner(desk):
    app, peer, context, send, add, _, ran, base = desk
    workflow = add(context["projects"][0]["root"], "Phone check")
    params = {"workflow_id": workflow.workflow_id}
    revision = send("desktop.read", "workflows.read", params)["value"]["revision"]

    started = send("desktop.action", "workflows.run", {**params, "expected_revision": revision})
    answer = started["value"]
    assert answer["status"] == "running" and answer["message"] == "Started on your computer."
    run_id = answer["run_id"]
    assert run_id and "nodes" not in answer

    view = wait_for(
        lambda: send("desktop.read", "workflows.read", params)["value"],
        lambda v: v["run"] and v["run"]["nodes"][0]["status"] == "running",
    )
    assert view["status"] == "running" and view["runs"][0]["run_id"] == run_id
    assert view["run"]["run_id"] == run_id and view["run"]["summary"] == "Running"
    assert [(n["node_id"], n["status"]) for n in view["run"]["nodes"]] == [
        ("build", "running"),
        ("lint", "pending"),
        ("report", "pending"),
    ]
    assert view["controls"]["stop"]["allowed"] is True
    assert send("desktop.read", "workflows.list")["value"]["workflows"][0]["status"] == "running"

    stopped = send("desktop.action", "workflows.stop", {**params, "run_id": run_id})["value"]
    assert plain(stopped) == {
        "workflow_id": workflow.workflow_id,
        "run_id": run_id,
        "status": "stopping",
        "message": "Stopping this run.",
    }
    final = wait_for(
        lambda: send("desktop.read", "workflows.read", {**params, "run_id": run_id})["value"],
        lambda v: v["run"]["status"] != "running",
    )
    assert final["run"]["status"] == "stopped" and final["status"] == "stopped"
    assert final["controls"]["stop"]["allowed"] is False
    assert {n["node_id"]: n["status"] for n in final["run"]["nodes"]}["report"] == "skipped"
    assert ran == ["build"]
    # Nothing left to stop: the desktop's own message comes back.
    again = send("desktop.action", "workflows.stop", {**params, "run_id": run_id})["value"]
    assert again["status"] == "stopped" and "no longer active" in again["message"]


def test_a_resent_run_request_starts_one_run(desk):
    app, peer, context, send, add, release, _, base = desk
    workflow = add(context["projects"][0]["root"], "Phone check")
    params = {
        "workflow_id": workflow.workflow_id,
        "expected_revision": source_revision(workflow.entrypoint.read_text()),
    }
    first = event(
        "desktop.action", 900, thread_id=None, payload={"op": "workflows.run", "params": params}
    )
    app.handle(peer, first)
    app.handle(peer, first)
    # A new event that repeats the request ID answers with the run already started.
    repeat = {**first, "id": "10000000-0000-4000-8000-000000000901", "sequence": 901}
    replies = app.handle(peer, repeat)
    body = json.loads([r for r in replies if r["type"] == "desktop.result"][-1]["payload"]["body"])
    assert body["message"] == "This run was already started."
    assert len(api.recent_rattish_runs_payload(workflow.workflow_id, base)["runs"]) == 1
    release.set()


def test_runs_keep_the_desktop_safeguards(desk):
    app, peer, context, send, add, _, ran, base = desk
    root = context["projects"][0]["root"]
    workflow = add(root, "Phone check")
    params = {"workflow_id": workflow.workflow_id}
    revision = source_revision(workflow.entrypoint.read_text())

    # The phone must run the revision it was shown.
    missing = send("desktop.action", "workflows.run", params)
    assert missing == {"ok": False, "code": "invalid", "detail": module.REFRESH}
    workflow.entrypoint.write_text(SOURCE.replace("command: fake-only\n  to", "command: x\n  to"))
    stale = send("desktop.action", "workflows.run", {**params, "expected_revision": revision})
    assert stale == {"ok": False, "code": "conflict", "detail": module.CHANGED}

    # Inputs without defaults can only be given on the desktop.
    needs = add(
        root,
        "Needs input",
        "Rattish: 1\nWorkflow:\n  name: Phone check\n  inputs:\n    target:\n"
        '      schema: {"type": "string"}\n      required: true\n'
        "Node work:\n  type: bash-command\n  command: fake-only\n",
    )
    view = send("desktop.read", "workflows.read", {"workflow_id": needs.workflow_id})["value"]
    assert view["controls"]["run"] == {"allowed": False, "reason": module.NEEDS_INPUTS}
    refused = send(
        "desktop.action",
        "workflows.run",
        {"workflow_id": needs.workflow_id, "expected_revision": view["revision"]},
    )
    assert refused == {"ok": False, "code": "invalid", "detail": module.NEEDS_INPUTS}

    # A workflow with errors shows why it can't run.
    broken = add(root, "Broken", "Rattish: 1\nWorkflow:\n  name: Broken\nNode x:\n  type: nope\n")
    listed = {
        w["workflow_id"]: w for w in send("desktop.read", "workflows.list")["value"]["workflows"]
    }
    assert listed[broken.workflow_id]["status"] == "invalid"
    view = send("desktop.read", "workflows.read", {"workflow_id": broken.workflow_id})["value"]
    assert view["controls"]["run"] == {"allowed": False, "reason": module.INVALID}
    assert view["nodes"] == [] and view["revision"] == ""
    assert ran == []


def test_resume_starts_a_new_run_from_the_phone(desk):
    app, peer, context, send, add, release, ran, base = desk
    workflow = add(context["projects"][0]["root"], "Phone check")
    params = {"workflow_id": workflow.workflow_id}
    revision = source_revision(workflow.entrypoint.read_text())
    earlier = send("desktop.action", "workflows.run", {**params, "expected_revision": revision})[
        "value"
    ]["run_id"]
    wait_for(lambda: {"ran": list(ran)}, lambda v: v["ran"] == ["build"])
    send("desktop.action", "workflows.stop", {**params, "run_id": earlier})
    shown = {**params, "run_id": earlier}
    view = wait_for(
        lambda: send("desktop.read", "workflows.read", shown)["value"],
        lambda v: v["run"]["status"] == "stopped",
    )
    # `build` was cut off by the stop, so a resume runs it again.
    assert view["controls"]["resume"] == {"allowed": True, "reason": "", "rerun_nodes": ["build"]}

    resume = event(
        "desktop.action",
        950,
        thread_id=None,
        payload={"op": "workflows.resume", "params": shown},
    )
    replies = app.handle(peer, resume)
    answer = json.loads(
        [r for r in replies if r["type"] == "desktop.result"][-1]["payload"]["body"]
    )
    assert answer["status"] == "running" and answer["run_id"] not in {"", earlier}
    assert answer["message"] == "Resumed on your computer as a new run."
    # A new event repeating the request ID resumes nothing more.
    repeat = {**resume, "id": "10000000-0000-4000-8000-000000000951", "sequence": 951}
    again = json.loads(
        [r for r in app.handle(peer, repeat) if r["type"] == "desktop.result"][-1]["payload"][
            "body"
        ]
    )
    assert again["message"] == "This run was already resumed."
    assert again["run_id"] == answer["run_id"]

    release.set()
    newer = {**params, "run_id": answer["run_id"]}
    done = wait_for(
        lambda: send("desktop.read", "workflows.read", newer)["value"],
        lambda v: v["run"]["status"] != "running",
    )
    assert done["run"]["status"] == "succeeded"
    assert done["run"]["resumed_from"] == earlier
    assert done["controls"]["resume"]["allowed"] is False
    assert len(api.recent_rattish_runs_payload(workflow.workflow_id, base)["runs"]) == 2

    # The earlier run was resumed already; the phone is told to resume the newer one.
    old = send("desktop.read", "workflows.read", shown)["value"]["controls"]["resume"]
    assert old["allowed"] is False and answer["run_id"] in old["reason"]
    refused = send("desktop.action", "workflows.resume", shown)
    assert refused["code"] == "invalid" and "already resumed" in refused["detail"]
    assert send("desktop.action", "workflows.resume", params)["code"] == "invalid"
    assert len(api.recent_rattish_runs_payload(workflow.workflow_id, base)["runs"]) == 2


def test_a_slow_start_answers_without_waiting_for_it(desk, monkeypatch):
    app, peer, context, send, add, _, _, base = desk
    workflow = add(context["projects"][0]["root"], "Phone check")
    gate = threading.Event()

    async def slow(*args, **kwargs):
        while not gate.is_set():
            await anyio.sleep(0.01)
        return {"runId": "late", "status": "running"}

    monkeypatch.setattr(api, "run_workflow_payload", slow)
    monkeypatch.setattr(module, "START_WAIT", 0.05)
    params = {
        "workflow_id": workflow.workflow_id,
        "expected_revision": source_revision(workflow.entrypoint.read_text()),
    }
    answer = send("desktop.action", "workflows.run", params)["value"]
    assert plain(answer) == {
        "workflow_id": workflow.workflow_id,
        "run_id": "",
        "status": "queued",
        "message": module.STILL_STARTING,
    }
    gate.set()


def test_unpairing_forgets_workflow_requests(desk):
    app, peer, context, send, add, release, _, base = desk
    workflow = add(context["projects"][0]["root"], "Phone check")
    revision = source_revision(workflow.entrypoint.read_text())
    send(
        "desktop.action",
        "workflows.run",
        {"workflow_id": workflow.workflow_id, "expected_revision": revision},
    )
    rows = "SELECT COUNT(*) FROM device_workflow_requests WHERE peer=?"
    assert app.registry.db.execute(rows, (peer,)).fetchone()[0] == 1
    release.set()
    app.remove_peer(peer)
    assert app.registry.db.execute(rows, (peer,)).fetchone()[0] == 0


def test_run_and_node_statuses_map_to_the_phone_vocabulary(desk):
    app = desk[0]
    phone_workflows = app.desktop.workflows
    graph = [{"node_id": name} for name in ("a", "b", "c", "d", "e")]
    detail = {
        "runId": "r1",
        "status": "running",
        "rattishRun": {"status": "running", "started_at": "t0", "duration_ms": 0},
        "runNodes": {
            "a": {"status": "success", "startedAt": "t1", "finishedAt": "t2"},
            "b": {"status": "allowed_failure"},
            "c": {"status": "disconnected"},
        },
        "error": None,
    }
    registered = type("W", (), {"project_root": Path("/p")})()
    view = phone_workflows._detail(detail, {"r1": ["d"]}, registered, "Notes", graph)
    assert view["status"] == "waiting" and view["summary"] == "Waiting for approval"
    assert [(n["status"], n["detail"]) for n in view["nodes"]] == [
        ("succeeded", ""),
        ("failed", "Failed, allowed to continue"),
        ("interrupted", ""),
        ("waiting", "Waiting for approval"),
        ("pending", ""),
    ]
    finished = phone_workflows._run(
        {"id": "r2", "status": "disconnected", "durationSeconds": 125.4}
    )
    assert finished["status"] == "interrupted" and finished["summary"] == "Took 2 min 5 s"
