"""Workflows on a paired phone: scoped views, desktop-owned runs and kept safeguards."""

import functools
import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import pytest

import gofer.ui.api as api
from gofer.devices import workflows as module
from gofer.rattish.editor import source_revision
from gofer.rattish.run_service import rerun_rattish_run, resume_rattish_run, run_rattish_file
from gofer.rattish.runtime import HandlerResult, NodeHandlerRegistry
from gofer.rattish.workspaces import create_registered_workflow
from tests.unit.test_device_application import application as application
from tests.unit.test_device_application import event
from tests.unit.test_device_desktop_control import phone as phone
from tests.unit.test_device_phone_guard import _ask

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

    async def fake_rerun(*args, **kwargs):
        return await rerun_rattish_run(
            *args, **kwargs, handlers=NodeHandlerRegistry({"raticode.bash_command": handler})
        )

    monkeypatch.setattr(api, "run_rattish_file", fake_run)
    monkeypatch.setattr(api, "resume_rattish_run", fake_resume)
    monkeypatch.setattr(api, "rerun_rattish_run", fake_rerun)

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
    assert send("desktop.read", "overview")["value"]["workflow_triggers"] is True
    assert plain(send("desktop.read", "workflows.list")["value"]) == {
        "workflows": [],
        "earlier": 0,
        "triggers": {
            "paused": False,
            "paused_at": "",
            "count": 0,
            "enabled": 0,
            "daily_limit": "",
            "limit_reached": False,
        },
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
    assert entry["text_inputs"] == []

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


def test_phone_lists_and_runs_workflows_found_outside_raticode(desk):
    from gofer.rattish.workspaces import discover_project_workflows

    app, peer, context, send, _, release, ran, base = desk
    root = Path(context["projects"][0]["root"])
    for team in ("team-a", "team-b"):
        folder = root / team / "daily"
        folder.mkdir(parents=True)
        (folder / "workflow.rattish").write_text(SOURCE, encoding="utf-8")
    discovered = discover_project_workflows(root, registry_dir=base).workflows
    ids = {workflow.relative_folder: workflow.workflow_id for workflow in discovered}

    listed = send("desktop.read", "workflows.list")["value"]["workflows"]
    by_id = {entry["workflow_id"]: entry for entry in listed}
    # Same name, told apart by their folder; still no absolute paths.
    assert by_id[ids["team-a/daily"]]["description"] == "team-a/daily · 3 nodes, 2 routes."
    assert by_id[ids["team-b/daily"]]["description"] == "team-b/daily · 3 nodes, 2 routes."
    assert str(root) not in json.dumps(listed) and str(base) not in json.dumps(listed)

    params = {"workflow_id": ids["team-a/daily"]}
    revision = send("desktop.read", "workflows.read", params)["value"]["revision"]
    started = send("desktop.action", "workflows.run", {**params, "expected_revision": revision})
    assert started["value"]["status"] == "running"
    release.set()
    final = wait_for(
        lambda: send("desktop.read", "workflows.read", params)["value"],
        lambda v: v["run"] and v["run"]["status"] not in {"running", "stopping"},
    )
    assert final["run"]["status"] == "succeeded", final["run"]
    assert ran[:1] == ["build"]


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
        # "stopping" (Stop requested) shows until the runner has ended the run.
        lambda v: v["run"]["status"] not in {"running", "stopping"},
    )
    assert final["run"]["status"] == "stopped" and final["status"] == "stopped"
    assert final["controls"]["stop"]["allowed"] is False
    assert {n["node_id"]: n["status"] for n in final["run"]["nodes"]}["report"] == "skipped"
    assert ran == ["build"]
    # Nothing left to stop: the desktop's own message comes back.
    again = send("desktop.action", "workflows.stop", {**params, "run_id": run_id})["value"]
    assert again["status"] == "stopped" and "no longer running" in again["message"]


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
    broken = add(
        root, "Broken", "Rattish: 1\nWorkflow:\n  name: Broken\nNode x:\n  type: write-fil\n"
    )
    listed = {
        w["workflow_id"]: w for w in send("desktop.read", "workflows.list")["value"]["workflows"]
    }
    assert listed[broken.workflow_id]["status"] == "invalid"
    view = send("desktop.read", "workflows.read", {"workflow_id": broken.workflow_id})["value"]
    # The same first error and fix hint the desktop's workflow list shows.
    assert view["controls"]["run"] == {
        "allowed": False,
        "reason": "This workflow has an error. Line 5: No node contract is available for "
        "'write-fil'. Did you mean 'write-file'? Fix it on your computer.",
    }

    # A workflow that compiles but fails preflight names the first problem too.
    unready = add(
        root,
        "Unready",
        "Rattish: 1\nWorkflow:\n  name: Unready\nNode x:\n  type: read-file\n"
        "  path: missing-notes.txt\n",
    )
    ready_view = send("desktop.read", "workflows.read", {"workflow_id": unready.workflow_id})
    reason = ready_view["value"]["controls"]["run"]["reason"]
    assert ready_view["value"]["controls"]["run"]["allowed"] is False
    assert reason.startswith("This workflow can't run yet: ")
    assert "missing-notes.txt" in reason
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
            "a": {
                "status": "success",
                "startedAt": "t1",
                "finishedAt": "t2",
                "providerApp": "Claude Code 2.3.1",
            },
            "b": {"status": "allowed_failure"},
            "c": {"status": "disconnected"},
        },
        "error": None,
    }
    registered = type("W", (), {"project_root": Path("/p")})()
    view = phone_workflows._detail(detail, {"r1": ["d"]}, registered, "Notes", graph)
    assert view["status"] == "waiting" and view["summary"] == "Waiting for approval"
    assert [(n["status"], n["detail"]) for n in view["nodes"]] == [
        ("succeeded", "Provider: Claude Code 2.3.1."),
        ("failed", "Failed, allowed to continue"),
        ("interrupted", ""),
        ("waiting", "Waiting for approval"),
        ("pending", ""),
    ]
    finished = phone_workflows._run(
        {"id": "r2", "status": "disconnected", "durationSeconds": 125.4}
    )
    assert finished["status"] == "interrupted" and finished["summary"] == "Took 2 min 5 s"


def test_a_missing_workflow_secret_is_named_on_the_phone(desk, monkeypatch):
    from gofer.rattish import workflow_secrets
    from tests.unit.test_rattish_workflow_secrets import MemoryStore

    app, peer, context, send, add, _, ran, base = desk
    monkeypatch.setattr(workflow_secrets, "_secret_store", MemoryStore)
    monkeypatch.delenv("PHONE_TOKEN", raising=False)
    workflow_secrets._cache.clear()
    root = context["projects"][0]["root"]
    workflow = add(
        root,
        "Needs secret",
        "Rattish: 1\nWorkflow:\n  name: Needs secret\n"
        "Node work:\n  type: bash-command\n  command: fake-only\n"
        '  with:\n    token: secret["PHONE_TOKEN"]\n',
    )
    params = {"workflow_id": workflow.workflow_id}

    view = send("desktop.read", "workflows.read", params)["value"]
    reason = view["controls"]["run"]["reason"]
    assert view["controls"]["run"]["allowed"] is False
    assert "PHONE_TOKEN" in reason
    assert "Workflow settings > Secrets" in reason
    assert "Raticode rattish secrets set PHONE_TOKEN" in reason
    assert any("PHONE_TOKEN" in item for item in view["preview"]["blocking"])
    assert ran == []

    store = MemoryStore()
    monkeypatch.setattr(workflow_secrets, "_secret_store", lambda: store)
    workflow_secrets.save_workflow_secret("PHONE_TOKEN", "phone-test-value", base)
    view = send("desktop.read", "workflows.read", params)["value"]
    assert view["controls"]["run"] == {"allowed": True, "reason": ""}
    assert view["preview"]["blocking"] == []
    assert "phone-test-value" not in json.dumps(view)


INPUTS_SOURCE = (
    "Rattish: 1\nWorkflow:\n  name: Phone check\n  inputs:\n"
    "    target:\n"
    '      schema: {"type": "string", "title": "Target", "description": "Where to ship"}\n'
    "      required: true\n"
    "    count:\n"
    '      schema: {"type": "integer", "minimum": 1, "maximum": 9}\n'
    "      default: 3\n"
    "    dry:\n"
    '      schema: {"type": "boolean"}\n'
    "      default: false\n"
    "    mode:\n"
    '      schema: {"enum": ["fast", "safe"]}\n'
    "      default: safe\n"
    "    extra:\n"
    '      schema: {"type": "object"}\n'
    "    token:\n"
    '      schema: {"type": "string", "format": "password"}\n'
    "      default: kept-on-desktop\n"
    "    login:\n"
    '      schema: {"type": "object", "properties": {"pass": {"type": "string", '
    '"format": "password"}}}\n'
    '      default: {"pass": "nested-kept-on-desktop"}\n'
    "Node work:\n  type: bash-command\n  command: fake-only\n"
)


def test_the_run_form_is_built_from_the_input_schema(desk):
    app, peer, context, send, add, _, ran, base = desk
    workflow = add(context["projects"][0]["root"], "Phone check", INPUTS_SOURCE)
    params = {"workflow_id": workflow.workflow_id}

    # A phone without the form is still told to start this on the computer.
    old = send("desktop.read", "workflows.read", params)["value"]
    assert old["controls"]["run"] == {"allowed": False, "reason": module.NEEDS_INPUTS}

    view = send("desktop.read", "workflows.read", {**params, "input_form": True})["value"]
    assert view["controls"]["run"] == {"allowed": True, "reason": ""}
    fields = {field["name"]: field for field in view["inputs"]}
    assert set(fields) == {"count", "dry", "extra", "login", "mode", "target", "token"}
    assert fields["target"] == {
        "name": "target",
        "label": "Target",
        "description": "Where to ship",
        "kind": "text",
        "required": True,
        "secret": False,
        "has_default": False,
    }
    assert fields["count"]["kind"] == "integer" and fields["count"]["default"] == 3
    assert (fields["count"]["minimum"], fields["count"]["maximum"]) == (1, 9)
    assert fields["dry"]["kind"] == "boolean" and fields["dry"]["default"] is False
    assert fields["mode"]["kind"] == "choice" and fields["mode"]["choices"] == ["fast", "safe"]
    assert fields["mode"]["default"] == "safe"
    assert fields["extra"]["kind"] == "json" and fields["extra"]["json_type"] == "object"
    assert fields["extra"]["required"] is False and "default" not in fields["extra"]
    # A secret input is masked on the phone, and its default never leaves the computer.
    assert fields["token"]["secret"] is True and fields["token"]["has_default"] is True
    assert "default" not in fields["token"]
    assert "kept-on-desktop" not in json.dumps(view)
    # A password inside an input counts too, as it does when the run saves its inputs.
    assert fields["login"]["secret"] is True and "default" not in fields["login"]
    assert ran == []


def test_the_list_names_text_inputs_shared_text_can_fill(desk):
    app, peer, context, send, add, _, _, base = desk
    root = context["projects"][0]["root"]
    workflow = add(root, "Phone check", INPUTS_SOURCE)
    broken = add(root, "Broken", "Rattish: 1\nWorkflow:\n  name: Broken\nNode x:\n  type: nope\n")
    listed = {
        entry["workflow_id"]: entry
        for entry in send("desktop.read", "workflows.list")["value"]["workflows"]
    }
    # Choices, numbers, JSON and password inputs can't take shared text.
    assert listed[workflow.workflow_id]["text_inputs"] == [{"name": "target", "label": "Target"}]
    assert listed[broken.workflow_id]["text_inputs"] == []
    assert "kept-on-desktop" not in json.dumps(listed)


def test_phone_inputs_are_checked_then_reach_the_run(desk):
    app, peer, context, send, add, release, ran, base = desk
    workflow = add(context["projects"][0]["root"], "Phone check", INPUTS_SOURCE)
    revision = source_revision(workflow.entrypoint.read_text())
    params = {"workflow_id": workflow.workflow_id, "expected_revision": revision}

    def run(inputs: Any) -> dict[str, Any]:
        result: dict[str, Any] = send(
            "desktop.action", "workflows.run", {**params, "inputs": inputs}
        )
        return result

    assert run({}) == {"ok": False, "code": "invalid", "detail": "Fill in Target."}
    # The protocol refuses inputs that aren't named values; the desktop checks again.
    listed = event(
        "desktop.action",
        990,
        thread_id=None,
        payload={"op": "workflows.run", "params": {**params, "inputs": ["ship"]}},
    )
    assert app.handle(peer, listed)[-1]["type"] == "error"
    with pytest.raises(module.Refused) as caught:
        app.desktop.workflows._inputs(
            module._compile(workflow, base),
            {"inputs": ["ship"]},
            workflow,
            "Notes",
        )
    assert str(caught.value) == module.BAD_INPUTS
    wrong = run({"target": "prod", "count": "three"})
    assert wrong["code"] == "invalid" and "'count'" in wrong["detail"]
    assert "is not of type 'integer'" in wrong["detail"]
    above = run({"target": "prod", "count": 12})
    assert above["code"] == "invalid" and "'count'" in above["detail"]
    choice = run({"target": "prod", "mode": "reckless"})
    assert choice["code"] == "invalid" and "'mode'" in choice["detail"]
    unknown = run({"target": "prod", "surprise": 1})
    assert unknown["code"] == "invalid" and "surprise" in unknown["detail"]
    # Plain words, so the phone guard has nothing to flag before the size check.
    words = "ship the notes " * (module.INPUTS_LIMIT // 15 + 1)
    assert run({"target": words})["detail"] == module.TOO_LARGE
    nested: Any = "deep"
    for _ in range(module.INPUT_DEPTH + 2):
        nested = [nested]
    assert run({"target": "prod", "extra": {"a": nested}})["detail"] == module.TOO_LARGE
    # Nothing ran and no run was recorded for any refused request.
    assert ran == []
    assert api.recent_rattish_runs_payload(workflow.workflow_id, base)["runs"] == []

    answer = run(
        {"target": "phone-typed-target", "count": 4, "dry": True, "extra": {"tags": ["a"]}}
    )["value"]
    assert answer["status"] == "running" and answer["message"] == "Started on your computer."
    wait_for(lambda: {"ran": list(ran)}, lambda v: v["ran"] == ["work"])
    record = json.loads(
        api._rattish_run_artifact_path(base, workflow.workflow_id, answer["run_id"]).read_text()
    )
    assert record["input_names"] == ["count", "dry", "extra", "target"]
    release.set()
    view = wait_for(
        lambda: send(
            "desktop.read",
            "workflows.read",
            {"workflow_id": workflow.workflow_id, "run_id": answer["run_id"]},
        )["value"],
        lambda v: v["run"]["status"] != "running",
    )
    assert view["run"]["status"] == "succeeded"
    # Values typed on the phone never come back in a view.
    assert "phone-typed-target" not in json.dumps(view)


def test_a_run_asked_to_stop_shows_stop_requested_on_the_phone():
    run = module.PhoneWorkflows._run(
        {"id": "r1", "status": "running", "stopRequested": True, "awaitingApproval": True}
    )
    assert (run["status"], run["summary"]) == ("stopping", "Stop requested")
    # Once the run has ended the request no longer matters.
    ended = module.PhoneWorkflows._run({"id": "r1", "status": "stopped", "stopRequested": True})
    assert ended["status"] == "stopped"


def test_all_runs_list_open_projects_newest_first_with_phone_starts(desk, tmp_path):
    app, peer, context, send, add, release, ran, base = desk
    root = context["projects"][0]["root"]
    first = add(root, "Phone check")
    second = add(root, "Second check")
    elsewhere = tmp_path / "elsewhere-runs"
    elsewhere.mkdir()
    hidden = add(elsewhere, "Hidden")
    release.set()
    for workflow in (first, hidden):
        # The fixture's fake runner, started on the desktop rather than from the phone.
        anyio.run(functools.partial(api.run_rattish_file, workflow.entrypoint, data_dir=base))
    params = {"workflow_id": second.workflow_id}
    revision = send("desktop.read", "workflows.read", params)["value"]["revision"]
    started = send("desktop.action", "workflows.run", {**params, "expected_revision": revision})
    phone_run = started["value"]["run_id"]
    view = wait_for(
        lambda: send("desktop.read", "workflows.runs", {"since": "24h"})["value"],
        lambda v: len(v["runs"]) == 2 and v["runs"][0]["status"] == "succeeded",
    )
    assert [(r["workflow_name"], r["started_by"]) for r in view["runs"]] == [
        ("Second check", "phone"),
        ("Phone check", "manual"),
    ]
    newest = view["runs"][0]
    assert newest["run_id"] == phone_run
    assert newest["workflow_id"] == second.workflow_id and newest["project_name"] == "Notes"
    assert newest["tokens"] == "0" and newest["cost"] == "$0"
    assert view["summary"] == "2 workflow runs: 2 passed"
    assert view["earlier"] == 0
    assert {w["workflow_id"] for w in view["workflows"]} == {first.workflow_id, second.workflow_id}
    text = json.dumps(view)
    assert str(base) not in text and "Hidden" not in text

    only = send("desktop.read", "workflows.runs", {"workflow_id": first.workflow_id})["value"]
    assert [r["workflow_name"] for r in only["runs"]] == ["Phone check"]
    failed = send("desktop.read", "workflows.runs", {"status": "error"})["value"]
    assert failed["runs"] == [] and failed["summary"] == "No workflow runs"
    assert send("desktop.read", "workflows.runs", {"status": "bogus"})["code"] == "invalid"


def test_run_again_from_the_phone_uses_the_saved_inputs(desk):
    app, peer, context, send, add, release, ran, base = desk
    workflow = add(context["projects"][0]["root"], "Phone check", INPUTS_SOURCE)
    revision = source_revision(workflow.entrypoint.read_text())
    release.set()

    def start(inputs: dict[str, Any]) -> str:
        answer = send(
            "desktop.action",
            "workflows.run",
            {"workflow_id": workflow.workflow_id, "expected_revision": revision, "inputs": inputs},
        )["value"]
        return str(answer["run_id"])

    def settled(run_id: str) -> dict[str, Any]:
        return wait_for(
            lambda: send(
                "desktop.read",
                "workflows.read",
                {"workflow_id": workflow.workflow_id, "run_id": run_id},
            )["value"],
            lambda v: v["run"]["status"] not in {"running", "queued"},
        )

    first = start({"target": "phone-typed-target", "count": 4})
    view = settled(first)
    assert view["controls"]["rerun"] == {"allowed": True, "reason": ""}
    # The saved values stay on the computer; the view only says Run again is possible.
    assert "phone-typed-target" not in json.dumps(view)

    shown = {"workflow_id": workflow.workflow_id, "run_id": first}
    again = event(
        "desktop.action", 960, thread_id=None, payload={"op": "workflows.rerun", "params": shown}
    )
    answer = json.loads(app.handle(peer, again)[-1]["payload"]["body"])
    assert answer["message"] == "Started again on your computer with the same inputs."
    rerun_id = answer["run_id"]
    assert rerun_id and rerun_id != first
    repeat = {**again, "id": "10000000-0000-4000-8000-000000000961", "sequence": 961}
    repeated = json.loads(app.handle(peer, repeat)[-1]["payload"]["body"])
    assert repeated["run_id"] == rerun_id
    assert repeated["message"] == "This run was already started again."
    settled(rerun_id)
    record = json.loads(
        api._rattish_run_artifact_path(base, workflow.workflow_id, rerun_id).read_text()
    )
    assert record["rerun_of"] == first and record["started_by"] == "phone"
    assert record["input_names"] == ["count", "target"]

    # A password input isn't saved, so the phone names it and points to the computer.
    secret = start({"target": "prod", "token": "typed-password"})
    blocked = settled(secret)["controls"]["rerun"]
    assert blocked["allowed"] is False
    assert "token is a password" in blocked["reason"] and "on your computer" in blocked["reason"]
    refused = send(
        "desktop.action", "workflows.rerun", {"workflow_id": workflow.workflow_id, "run_id": secret}
    )
    assert refused["code"] == "invalid" and "Enter token again" in refused["detail"]
    # The protocol itself refuses Run again without a run, or with values.
    for params in ({"workflow_id": workflow.workflow_id}, {**shown, "inputs": {"token": "x"}}):
        bare = event(
            "desktop.action",
            970,
            thread_id=None,
            payload={"op": "workflows.rerun", "params": params},
        )
        assert app.handle(peer, bare)[-1]["type"] == "error"


def test_the_phone_names_password_inputs_it_cannot_fill_on_resume() -> None:
    controls = module.PhoneWorkflows._controls(
        object.__new__(module.PhoneWorkflows),
        {"workflow": {"inputs": []}, "nodes": []},
        {"runnable": True},
        [],
        {"allowed": True, "reason": "", "rerunNodes": ["ship"], "needsInputs": ["pin", "token"]},
        rerun={"allowed": True, "reason": "", "needsInputs": []},
    )
    assert controls["resume"]["allowed"] is False
    assert controls["resume"]["rerun_nodes"] == []
    assert "pin, token are passwords" in controls["resume"]["reason"]
    assert "Use Resume on your computer" in controls["resume"]["reason"]
    assert controls["rerun"] == {"allowed": True, "reason": ""}


def test_phone_lists_a_workflows_triggers_and_turns_them_off_and_on(desk, tmp_path):
    from gofer.devices.phone_guard import LOCKED_DETAIL
    from gofer.rattish.triggers import TriggerStore

    app, peer, context, send, add, _, _, base = desk
    root = context["projects"][0]["root"]
    shown = add(root, "Phone check")
    other = add(root, "Other")
    store = TriggerStore(base)
    watched = Path(root) / "inbox"
    watched.mkdir()
    cron, _ = store.create(
        kind="cron",
        source_path=shown.entrypoint,
        config={"expression": "0 9 * * 1-5", "timezone": "UTC"},
        label="Weekday digest",
    )
    files, _ = store.create(
        kind="file", source_path=shown.entrypoint, config={"path": str(watched), "glob": "*.md"}
    )
    hook, token = store.create(kind="webhook", source_path=shown.entrypoint, enabled=False)
    elsewhere, _ = store.create(kind="webhook", source_path=other.entrypoint)
    store.add_event(cron, status="failed", at=datetime.now(UTC), message=f"Failed in {root}/build")

    view = send("desktop.read", "workflows.read", {"workflow_id": shown.workflow_id})["value"]
    text = json.dumps(view)
    assert root not in text and str(base) not in text and str(token) not in text
    assert hook.id in text and "rattish-hooks" not in text and elsewhere.id not in text
    assert view["triggers_paused"] is False
    listed = {item["trigger_id"]: item for item in view["triggers"]}
    assert list(listed) == [cron.id, files.id, hook.id]
    assert listed[cron.id]["kind"] == "cron" and listed[cron.id]["kind_label"] == "Schedule"
    assert listed[cron.id]["label"] == "Weekday digest"
    assert listed[cron.id]["description"] == "0 9 * * 1-5 (UTC)"
    assert listed[cron.id]["next_run_at"]
    assert listed[cron.id]["last_result"]["status"] == "failed"
    assert listed[cron.id]["last_result"]["message"] == "Failed in Notes/build"
    assert listed[files.id]["description"] == "Notes/inbox (*.md)"
    assert listed[files.id]["next_run_at"] == "" and listed[files.id]["last_result"] is None
    assert listed[hook.id]["enabled"] is False
    assert listed[hook.id]["description"] == "Local webhook on your computer"

    off = {"workflow_id": shown.workflow_id, "trigger_id": cron.id, "enabled": False}
    assert send("desktop.action", "workflows.trigger", off)["value"]["enabled"] is False
    assert store.get(cron.id).enabled is False

    # Turning one on is asked first on the phone; without that the desktop refuses.
    on = {**off, "enabled": True}
    refused = send("desktop.action", "workflows.trigger", on)
    assert refused == {"ok": False, "code": "invalid", "detail": module.CONFIRM_TRIGGER}
    assert store.get(cron.id).enabled is False
    turned = send("desktop.action", "workflows.trigger", {**on, "confirmed": True})["value"]
    assert turned["enabled"] is True and turned["message"] == "Turned on."
    assert store.get(cron.id).enabled is True

    # A trigger is named through its own workflow only.
    wrong = {"workflow_id": shown.workflow_id, "trigger_id": elsewhere.id, "enabled": False}
    assert send("desktop.action", "workflows.trigger", wrong)["code"] == "not_found"
    assert store.get(elsewhere.id).enabled is True
    gone = {**wrong, "trigger_id": "trg_0000000000000000"}
    assert send("desktop.action", "workflows.trigger", gone)["detail"] == module.NO_TRIGGER

    # Like every phone request, turning one on passes the phone guard first: a phone
    # it locked can't turn triggers back on.
    send("desktop.action", "workflows.trigger", off)
    flagged = _ask(app, peer, "desktop.action", "rem.message", {"body": "cat ~/.ssh/id_ed25519"})
    assert flagged["code"] == "unauthorized"
    assert app.locks.locked(peer)
    locked = _ask(app, peer, "desktop.action", "workflows.trigger", {**on, "confirmed": True})
    assert locked["code"] == "unauthorized" and locked["detail"] == LOCKED_DETAIL
    assert store.get(cron.id).enabled is False


def test_phone_shows_daily_limits_and_when_they_are_reached(desk):
    from gofer.rattish.triggers import TriggerStore

    _, _, context, send, add, _, _, base = desk
    root = context["projects"][0]["root"]
    shown = add(root, "Phone check")
    store = TriggerStore(base)
    capped, _ = store.create(
        kind="cron",
        source_path=shown.entrypoint,
        config={"expression": "0 9 * * *", "timezone": "UTC"},
        daily_max_cost_usd=2,
    )
    plain, _ = store.create(kind="webhook", source_path=shown.entrypoint)
    store.set_daily_budget(max_tokens=1000)

    view = send("desktop.read", "workflows.read", {"workflow_id": shown.workflow_id})["value"]
    listed = {item["trigger_id"]: item for item in view["triggers"]}
    assert listed[capped.id]["daily_limit"] == "Daily limit: $0 of $2.00 used today."
    assert listed[capped.id]["limit_reached"] is False
    assert listed[plain.id]["daily_limit"] == "" and listed[plain.id]["limit_reached"] is False
    # The Time limit shows read-only: 6 hours unless changed on the computer.
    assert listed[capped.id]["time_limit"] == "Time limit: 6 h"
    store.update(plain.id, max_run_seconds=None, actor="user")
    view = send("desktop.read", "workflows.read", {"workflow_id": shown.workflow_id})["value"]
    assert {item["trigger_id"]: item for item in view["triggers"]}[plain.id][
        "time_limit"
    ] == "No time limit"
    summary = send("desktop.read", "workflows.list")["value"]["triggers"]
    assert summary["daily_limit"] == "Daily limit: 0 of 1,000 tokens used today."

    store.start_spend("run-a", capped.id, datetime.now(UTC))
    store.finish_spend("run-a", cost_usd=2.5, tokens=1200)
    view = send("desktop.read", "workflows.read", {"workflow_id": shown.workflow_id})["value"]
    reached = {item["trigger_id"]: item for item in view["triggers"]}[capped.id]
    assert reached["limit_reached"] is True
    assert "skipped until midnight (UTC)" in reached["daily_limit"]
    summary = send("desktop.read", "workflows.list")["value"]["triggers"]
    assert summary["limit_reached"] is True
    assert summary["daily_limit"].startswith("Daily limit: 1,200 of 1,000 tokens used today.")


def test_phone_pauses_and_resumes_all_triggers_keeping_each_setting(desk):
    from gofer.rattish.triggers import TriggerStore

    _, _, context, send, add, _, _, base = desk
    root = context["projects"][0]["root"]
    shown = add(root, "Phone check")
    store = TriggerStore(base)
    on, _ = store.create(
        kind="cron", source_path=shown.entrypoint, config={"expression": "* * * * *"}
    )
    off, _ = store.create(kind="webhook", source_path=shown.entrypoint, enabled=False)

    paused = send("desktop.action", "workflows.triggers.pause", {"paused": True})["value"]
    assert paused["paused"] is True and paused["paused_at"]
    assert (paused["count"], paused["enabled"]) == (2, 1)
    assert store.pause_state().paused is True
    assert store.get(on.id).enabled is True and store.get(off.id).enabled is False
    listed = send("desktop.read", "workflows.list")["value"]
    assert listed["triggers"]["paused"] is True
    view = send("desktop.read", "workflows.read", {"workflow_id": shown.workflow_id})["value"]
    assert view["triggers_paused"] is True

    # A trigger turned on during a pause waits for the resume.
    turned = send(
        "desktop.action",
        "workflows.trigger",
        {
            "workflow_id": shown.workflow_id,
            "trigger_id": off.id,
            "enabled": True,
            "confirmed": True,
        },
    )["value"]
    assert "paused" in turned["message"]
    send(
        "desktop.action",
        "workflows.trigger",
        {"workflow_id": shown.workflow_id, "trigger_id": off.id, "enabled": False},
    )

    refused = send("desktop.action", "workflows.triggers.pause", {"paused": False})
    assert refused == {"ok": False, "code": "invalid", "detail": module.CONFIRM_RESUME}
    assert store.pause_state().paused is True
    resumed = send(
        "desktop.action", "workflows.triggers.pause", {"paused": False, "confirmed": True}
    )["value"]
    assert resumed["paused"] is False and resumed["message"].startswith("Triggers resumed: 1 of 2")
    assert store.get(on.id).enabled is True and store.get(off.id).enabled is False


def test_phone_reviews_and_approves_a_changed_trigger_workflow(desk):
    from gofer.devices.phone_guard import LOCKED_DETAIL
    from gofer.rattish.triggers import TriggerStore, approval_status

    app, peer, context, send, add, _, _, base = desk
    root = context["projects"][0]["root"]
    shown = add(root, "Phone check")
    store = TriggerStore(base)
    cron, _ = store.create(
        kind="cron", source_path=shown.entrypoint, config={"expression": "0 9 * * *"}
    )
    ask = {"workflow_id": shown.workflow_id, "trigger_id": cron.id}

    view = send("desktop.read", "workflows.read", {"workflow_id": shown.workflow_id})["value"]
    assert view["triggers"][0]["approval"] == "approved"
    assert view["triggers"][0]["approval_message"] == ""
    assert view["triggers"][0]["run_edits_without_approval"] is False
    assert send("desktop.read", "overview")["value"]["workflow_trigger_approval"] is True

    shown.entrypoint.write_text(
        shown.entrypoint.read_text(encoding="utf-8") + "# edited by an agent\n", encoding="utf-8"
    )
    view = send("desktop.read", "workflows.read", {"workflow_id": shown.workflow_id})["value"]
    assert view["triggers"][0]["approval"] == "changed"
    assert view["triggers"][0]["approval_message"] == "Workflow changed since you approved it."

    changes = send("desktop.read", "workflows.trigger.changes", ask)["value"]
    assert changes["approval"] == "changed"
    assert "+# edited by an agent" in changes["files"][0]["diff"]
    assert changes["files"][0]["diff_available"] is True
    assert root not in json.dumps(changes) and str(base) not in json.dumps(changes)
    assert changes["files"][0]["change"] == "changed"
    assert changes["files"][0]["kind"] == "Workflow"

    # Approving names the version the phone showed (the schema requires it); one that
    # moved on since is refused.
    shown.entrypoint.write_text(
        shown.entrypoint.read_text(encoding="utf-8") + "# and again\n", encoding="utf-8"
    )
    stale = send(
        "desktop.action",
        "workflows.trigger.approve",
        {**ask, "fingerprint": changes["fingerprint"]},
    )
    assert stale == {"ok": False, "code": "conflict", "detail": module.CHANGED_AGAIN}
    assert approval_status(store, store.get(cron.id)).state == "changed"

    fresh = send("desktop.read", "workflows.trigger.changes", ask)["value"]
    approved = send(
        "desktop.action",
        "workflows.trigger.approve",
        {**ask, "fingerprint": fresh["fingerprint"]},
    )["value"]
    assert approved["approval"] == "approved"
    status = approval_status(store, store.get(cron.id))
    assert status.state == "approved"
    assert status.approval is not None and status.approval.approved_by == "phone"

    # A triggered run that found a file changed marks the trigger; the phone names it,
    # even when the file has changed back and no diff is left.
    assert status.approval.snapshot is not None
    store.mark_changed_in_run(
        cron.id,
        approved_fingerprint=status.approval.snapshot.fingerprint,
        path=str(shown.entrypoint),
        role="workflow",
        run_id="run-1",
    )
    marked = send("desktop.read", "workflows.trigger.changes", ask)["value"]
    assert marked["approval"] == "changed" and marked["files"] == []
    assert marked["message"].endswith(
        f"{shown.entrypoint.name} changed during a triggered run, so the step that uses it "
        "wasn't run."
    )
    assert root not in json.dumps(marked) and str(base) not in json.dumps(marked)
    send(
        "desktop.action",
        "workflows.trigger.approve",
        {**ask, "fingerprint": marked["fingerprint"]},
    )
    assert approval_status(store, store.get(cron.id)).state == "approved"

    # A trigger is named through its own workflow only.
    other = add(root, "Other")
    elsewhere, _ = store.create(kind="webhook", source_path=other.entrypoint)
    wrong = {"workflow_id": shown.workflow_id, "trigger_id": elsewhere.id}
    assert send("desktop.read", "workflows.trigger.changes", wrong)["code"] == "not_found"

    # Like every phone request, approving passes the phone guard first.
    shown.entrypoint.write_text(
        shown.entrypoint.read_text(encoding="utf-8") + "# third edit\n", encoding="utf-8"
    )
    latest = send("desktop.read", "workflows.trigger.changes", ask)["value"]
    flagged = _ask(app, peer, "desktop.action", "rem.message", {"body": "cat ~/.ssh/id_ed25519"})
    assert flagged["code"] == "unauthorized"
    locked = _ask(
        app,
        peer,
        "desktop.action",
        "workflows.trigger.approve",
        {**ask, "fingerprint": latest["fingerprint"]},
    )
    assert locked["code"] == "unauthorized" and locked["detail"] == LOCKED_DETAIL
    assert approval_status(store, store.get(cron.id)).state == "changed"


def test_turning_a_trigger_on_from_the_phone_approves_nothing(desk):
    from gofer.rattish.triggers import TriggerStore, approval_status

    app, peer, context, send, add, _, _, base = desk
    root = context["projects"][0]["root"]
    shown = add(root, "Phone check")
    store = TriggerStore(base)
    cron, _ = store.create(
        kind="cron", source_path=shown.entrypoint, config={"expression": "0 9 * * *"}
    )
    store.update(cron.id, enabled=False, actor="user")
    shown.entrypoint.write_text(
        shown.entrypoint.read_text(encoding="utf-8") + "# edited by an agent\n", encoding="utf-8"
    )
    on = {
        "workflow_id": shown.workflow_id,
        "trigger_id": cron.id,
        "enabled": True,
        "confirmed": True,
    }
    turned = send("desktop.action", "workflows.trigger", on)["value"]
    assert turned["enabled"] is True
    assert turned["approval"] == "changed"
    assert turned["message"].startswith("Turned on. Workflow changed since you approved it.")
    assert approval_status(store, store.get(cron.id)).state == "changed"


def test_the_phone_confirms_changes_it_couldnt_show_in_full(desk):
    from gofer.rattish.triggers import TriggerStore, approval_status

    app, peer, context, send, add, _, _, base = desk
    project = context["projects"][0]
    root = Path(project["root"])
    (root / "prompts").mkdir(exist_ok=True)
    prompt = root / "prompts" / "ask.md"
    prompt.write_text("Say hello.\n", encoding="utf-8")
    source = (
        "Rattish: 1\nWorkflow:\n  name: Phone check\n"
        "Node render:\n  type: prompt-file\n  output-path: out.md\n"
        "  template-path: prompts/ask.md\n"
    )
    shown = add(root, "Phone check", source)
    store = TriggerStore(base)
    cron, _ = store.create(
        kind="cron", source_path=shown.entrypoint, config={"expression": "0 9 * * *"}
    )
    # Long enough to pass the phone's DIFF_LIMIT, short enough for the desktop's lines.
    line = "x" * 100
    prompt.write_text("".join(f"{index} {line}\n" for index in range(1000)), encoding="utf-8")
    ask = {"workflow_id": shown.workflow_id, "trigger_id": cron.id}
    changes = send("desktop.read", "workflows.trigger.changes", ask)["value"]
    assert changes["project_id"] == project["id"]
    assert changes["unseen_hidden"] == 0
    (file,) = changes["files"]
    assert file["truncated"] is True and file["unseen"] == "cut"
    assert file["open_path"] == "prompts/ask.md"
    assert len(file["diff"]) <= module.DIFF_LIMIT
    assert "hidden" not in file

    refused = send(
        "desktop.action",
        "workflows.trigger.approve",
        {**ask, "fingerprint": changes["fingerprint"]},
    )
    assert refused["code"] == "invalid"
    assert "prompts/ask.md (diff cut short)" in refused["detail"]
    assert str(root) not in refused["detail"]
    assert approval_status(store, store.get(cron.id)).state == "changed"

    approved = send(
        "desktop.action",
        "workflows.trigger.approve",
        {**ask, "fingerprint": changes["fingerprint"], "unseen_confirmed": True},
    )["value"]
    assert approved["approval"] == "approved"
    assert approval_status(store, store.get(cron.id)).state == "approved"

    # A short change shows in full, so no second confirmation is needed.
    prompt.write_text(
        "".join(f"{index} {'y' if index == 0 else 'x'}{line[1:]}\n" for index in range(1000)),
        encoding="utf-8",
    )
    small = send("desktop.read", "workflows.trigger.changes", ask)["value"]
    assert small["files"][0]["unseen"] == ""
    done = send(
        "desktop.action",
        "workflows.trigger.approve",
        {**ask, "fingerprint": small["fingerprint"]},
    )["value"]
    assert done["approval"] == "approved"


def test_the_phone_warns_about_an_unpinned_mcp_server_and_asks_again(desk):
    from gofer.core import rem_library
    from gofer.rattish.mcp_packages import UNPINNED_WARNING
    from gofer.rattish.triggers import TriggerStore, approval_status

    app, peer, context, send, add, _, _, base = desk
    root = Path(context["projects"][0]["root"])

    def server(*args: str, replace: str | None = "gh") -> None:
        definition = {"name": "gh", "type": "stdio", "command": "npx", "args": list(args)}
        rem_library.save_mcp_server(base, definition, replace=replace)

    server("-y", "server@1.0.0", replace=None)
    source = (
        "Rattish: 1\nWorkflow:\n  name: Phone check\n"
        "Node call:\n  type: mcp-tool\n  server: gh\n  tool: list_issues\n"
    )
    shown = add(root, "Phone check", source)
    store = TriggerStore(base)
    cron, _ = store.create(
        kind="cron", source_path=shown.entrypoint, config={"expression": "0 9 * * *"}
    )
    server("-y", "server")
    ask = {"workflow_id": shown.workflow_id, "trigger_id": cron.id}
    changes = send("desktop.read", "workflows.trigger.changes", ask)["value"]
    (file,) = changes["files"]
    assert file["kind"] == "MCP server"
    assert file["unseen"] == "unpinned"
    assert file["warning"] == UNPINNED_WARNING
    assert file["open_path"] == ""

    refused = send(
        "desktop.action",
        "workflows.trigger.approve",
        {**ask, "fingerprint": changes["fingerprint"]},
    )
    assert refused["code"] == "invalid"
    assert "mcp-server:gh (package version not pinned" in refused["detail"]
    assert approval_status(store, store.get(cron.id)).state == "changed"

    approved = send(
        "desktop.action",
        "workflows.trigger.approve",
        {**ask, "fingerprint": changes["fingerprint"], "unseen_confirmed": True},
    )["value"]
    assert approved["approval"] == "approved"

    # A pinned server shows no warning and needs no second yes.
    server("-y", "server@2.0.0")
    pinned = send("desktop.read", "workflows.trigger.changes", ask)["value"]
    assert pinned["files"][0]["unseen"] == "" and pinned["files"][0]["warning"] == ""
    done = send(
        "desktop.action",
        "workflows.trigger.approve",
        {**ask, "fingerprint": pinned["fingerprint"]},
    )["value"]
    assert done["approval"] == "approved"


FILE_SOURCE = (
    "Rattish: 1\nWorkflow:\n  name: Phone check\n  inputs:\n"
    '    document:\n      schema: {"type": "string", "format": "file", "title": "Document"}\n'
    '      required: true\n      accept: [".txt", "image/*"]\n      max-size: 64\n'
    '    tone:\n      schema: {"type": "string"}\n      required: false\n'
    "Node work:\n  type: bash-command\n  command: fake-only\n"
)


def test_a_shared_file_fills_a_file_input_and_is_copied_into_the_run(desk):
    from uuid import uuid4

    from gofer.rattish.file_inputs import run_files_dir, uploads_dir
    from tests.unit.test_device_desktop_control import _upload

    app, peer, context, send, add, release, ran, base = desk
    workflow = add(context["projects"][0]["root"], "Phone check", FILE_SOURCE)
    listed = send("desktop.read", "workflows.list")["value"]["workflows"][0]
    assert listed["file_inputs"] == [
        {"name": "document", "label": "Document", "accept": [".txt", "image/*"], "max_size": 64}
    ]
    assert listed["text_inputs"] == [{"name": "tone", "label": "tone"}]
    fields = {
        field["name"]: field
        for field in send("desktop.read", "workflows.read", {"workflow_id": workflow.workflow_id})[
            "value"
        ]["inputs"]
    }
    assert fields["document"]["kind"] == "file" and fields["document"]["max_size"] == 64

    revision = source_revision(workflow.entrypoint.read_text())
    params = {"workflow_id": workflow.workflow_id, "expected_revision": revision}
    channel = app.desktop.files_thread(peer)
    # Without the file the run is refused, as a required input is.
    assert send("desktop.action", "workflows.run", {**params, "inputs": {}})["detail"] == (
        "Fill in Document."
    )
    too_big = _upload(app, peer, channel, b"x" * 65, 900)
    refused = send(
        "desktop.action",
        "workflows.run",
        {**params, "inputs": {}, "file_ids": {"document": too_big}},
    )
    assert refused["code"] == "invalid" and "more than the 64 bytes" in refused["detail"]
    gone = send(
        "desktop.action",
        "workflows.run",
        {**params, "inputs": {}, "file_ids": {"document": str(uuid4())}},
    )
    assert gone["detail"] == module.UPLOAD_GONE
    assert ran == []

    # _upload names the file photo.png, which image/* accepts.
    shared = _upload(app, peer, channel, b"shared bytes", 910)
    answer = send(
        "desktop.action",
        "workflows.run",
        {**params, "inputs": {"tone": "brief"}, "file_ids": {"document": shared}},
    )["value"]
    assert answer["message"] == "Started on your computer."
    wait_for(lambda: {"ran": list(ran)}, lambda v: v["ran"] == ["work"])
    record = api._rattish_run_artifact_path(base, workflow.workflow_id, answer["run_id"])
    copy = run_files_dir(record) / "document" / "photo.png"
    assert copy.read_bytes() == b"shared bytes"
    assert json.loads(record.read_text())["input_names"] == ["document", "tone"]
    # The run took the upload, and the phone's copy on the channel is gone too.
    assert list(uploads_dir(base).iterdir()) == []
    with pytest.raises(Exception, match="file_unavailable"):
        app.files.metadata(peer, channel, shared)
    release.set()


def test_phone_reads_a_failure_for_ask_rem_without_folder_paths(desk, tmp_path):
    app, peer, context, send, add, _, _, base = desk
    root = context["projects"][0]["root"]
    workflow = add(root, "Phone check")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    hidden = add(elsewhere, "Hidden")
    assert send("desktop.read", "overview")["value"]["workflow_failures"] is True

    async def failing(node, run_context, bindings):
        output = {"stdout": f"wrote {root}/out.txt", "stderr": "", "exit_code": 2}
        return HandlerResult(False, output)

    async def run(entry) -> str:
        result = await run_rattish_file(
            entry.entrypoint,
            data_dir=base,
            handlers=NodeHandlerRegistry({"raticode.bash_command": failing}),
        )
        return str(result.document["run_id"])

    run_id = anyio.run(run, workflow)
    params = {"workflow_id": workflow.workflow_id, "run_id": run_id}
    answer = send("desktop.read", "workflows.failure", params)["value"]
    assert answer["run_id"] == run_id and answer["node_id"] == "build"
    assert answer["name"] == f"run-failure-{run_id}-build.txt"
    text = answer["text"]
    assert "UNTRUSTED RUN OUTPUT" in text and "Node: build (type: bash-command), failed" in text
    # The project and data folders are named, as everywhere else on the phone.
    assert root not in text and str(base) not in text
    assert "Workflow file: Notes/" in text and "wrote Notes/out.txt" in text

    named = send("desktop.read", "workflows.failure", {**params, "node_id": "build"})
    assert named["value"]["node_id"] == "build"
    missing = send("desktop.read", "workflows.failure", {**params, "node_id": "lint"})
    assert missing == {"ok": False, "code": "not_found", "detail": module.NO_FAILURE}
    gone = send("desktop.read", "workflows.failure", {**params, "run_id": "20990101T000000Z-x"})
    assert gone["code"] == "not_found"

    hidden_run = anyio.run(run, hidden)
    refused = send(
        "desktop.read",
        "workflows.failure",
        {"workflow_id": hidden.workflow_id, "run_id": hidden_run},
    )
    assert refused == {"ok": False, "code": "not_found", "detail": module.NOT_OPEN}


def test_phone_tries_another_model_and_uses_it_through_the_editor(desk, monkeypatch):
    from gofer.rattish.test_sets import save_test_set, workflow_tests_file
    from tests.unit.test_rattish_model_trials import SUMMARIZER, case_set, providers

    app, _, context, send, add, _, _, base = desk
    root = context["projects"][0]["root"]
    workflow = add(root, "Summarizer", SUMMARIZER)
    save_test_set(workflow_tests_file(workflow.entrypoint), case_set())
    current, candidate = providers()
    monkeypatch.setattr(
        "gofer.rattish.provider_runtime.default_provider_subscriptions",
        lambda: {"codex": current, "claude_code": candidate},
    )
    app.workspace.providers = [{"id": "claude_code", "models": ["claude-sonnet-5-5"]}]
    app.workspace._providers_read = time.monotonic()
    assert send("desktop.read", "overview")["value"]["workflow_tests"] is True
    ident = {"workflow_id": workflow.workflow_id}

    view = send("desktop.read", "workflows.tests", ident)["value"]
    assert root not in json.dumps(view) and str(base) not in json.dumps(view)
    assert view["cases"] == 2 and view["result"] is None
    assert [item["node"] for item in view["model_nodes"]] == ["write"]
    assert view["model_nodes"][0]["provider"] == "codex"
    assert view["providers"] == [{"id": "claude-code", "models": ["claude-sonnet-5-5"]}]
    models = [{"node": "write", "provider": "claude-code", "model": "claude-sonnet-5-5"}]
    tried = {**ident, "expected_revision": view["revision"], "models": models}

    stale = send(
        "desktop.read", "workflows.tests.estimate", {**tried, "expected_revision": "sha256:old"}
    )
    assert stale["code"] == "conflict"
    estimate = send("desktop.read", "workflows.tests.estimate", tried)["value"]
    assert estimate["current"]["reused"] is False and estimate["current"]["summary"]
    assert estimate["candidate"]["label"] == "write: claude-code claude-sonnet-5-5"
    assert estimate["total"] == "Unknown"

    # Running needs the phone's confirmation after it showed both costs: the protocol
    # schema refuses the request without it, and so does the desktop.
    from gofer.devices import workflow_tests
    from gofer.devices.desktop_control import Refused

    with pytest.raises(Refused, match="Check the expected cost"):
        workflow_tests.start_try(workflow, base, tried)
    started = send("desktop.action", "workflows.tests.try", {**tried, "confirmed": True})["value"]
    assert started["message"].startswith("Trying the model")
    done = wait_for(
        lambda: send("desktop.read", "workflows.tests", ident)["value"],
        lambda value: bool(value["result"]) and value["result"]["status"] not in {"running"},
        timeout=30,
    )
    result = done["result"]
    assert result["tried"] == "write: claude-code claude-sonnet-5-5"
    rows = {row["case_id"]: row for row in result["comparison"]["rows"]}
    assert rows["dogs"]["current"]["status"] == "failed"
    assert rows["dogs"]["candidate"]["status"] == "passed"
    assert rows["dogs"]["change"] == "fixed"
    assert rows["dogs"]["current"]["cost"] == "$0.02"
    assert rows["dogs"]["candidate"]["cost"] == "$0.0050"
    assert rows["dogs"]["candidate"]["time"].endswith("s")
    totals = result["comparison"]["totals"]
    assert (totals["current"]["passed"], totals["candidate"]["passed"]) == (1, 2)
    assert result["use"] == {"allowed": True, "reason": ""}
    source = workflow.entrypoint.read_text(encoding="utf-8")
    assert "claude-code" not in source

    use = {**ident, "expected_revision": result["revision"], "models": result["models"]}
    assert (
        send(
            "desktop.action", "workflows.tests.use_model", {**use, "expected_revision": "sha256:x"}
        )["code"]
        == "conflict"
    )
    used = send("desktop.action", "workflows.tests.use_model", use)["value"]
    assert used["message"].startswith("write now uses claude-code claude-sonnet-5-5")
    assert "provider: claude-code" in workflow.entrypoint.read_text(encoding="utf-8")
    assert used["result"]["stale"] is True and used["result"]["use"]["allowed"] is False
