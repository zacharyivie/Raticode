"""Workflow shortcuts: the store, what a press does, the API routes and the CLI."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from gofer.cli.client import AGENT_SESSION_ENVS
from gofer.cli.main import app
from gofer.rattish.trigger_approval import MAX_FILE_TEXT_CHARS, take_snapshot
from gofer.rattish.workflow_shortcuts import (
    MAX_SHORTCUTS,
    MAX_TEXT_CHARS,
    SHORTCUTS_FILENAME,
    ShortcutConflict,
    ShortcutError,
    ShortcutNotFound,
    ShortcutStore,
    approve_shortcut,
    binding_identity,
    clean_binding,
    plan_target,
)
from gofer.rattish.workspaces import create_registered_workflow
from gofer.ui.headless import agent_request_refusal
from tests.unit.test_ui_server import _request

NO_INPUTS = (
    "Rattish: 1\nWorkflow:\n  name: Tidy\nNode work:\n  type: bash-command\n  command: echo done\n"
)
TEXT_INPUT = (
    "Rattish: 1\nWorkflow:\n  name: Summarize\n  inputs:\n"
    "    article:\n"
    '      schema: {"type": "string", "title": "Article"}\n'
    "      required: true\n"
    "    tone:\n"
    '      schema: {"enum": ["short", "long"]}\n'
    "      default: short\n"
    "    note:\n"
    '      schema: {"type": "string", "title": "Note"}\n'
    "      default: nothing yet\n"
    "Node work:\n  type: bash-command\n  command: echo done\n"
)
NEEDS_NUMBER = (
    "Rattish: 1\nWorkflow:\n  name: Count\n  inputs:\n"
    "    count:\n"
    '      schema: {"type": "integer"}\n'
    "      required: true\n"
    "Node work:\n  type: bash-command\n  command: echo done\n"
)


def _workflow(tmp_path: Path, name: str, source: str) -> Any:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    workflow = create_registered_workflow(project, name, registry_dir=tmp_path)
    workflow.entrypoint.write_text(source, encoding="utf-8")
    return workflow


def _json(result: Any) -> dict[str, Any]:
    return cast(dict[str, Any], result.json())


def _add(tmp_path: Path, workflow: Any, binding: str, input_name: str | None = None) -> Any:
    """Adds a shortcut the way the desktop app does: approving the workflow as it is."""
    approval = take_snapshot(workflow.entrypoint, tmp_path)
    return ShortcutStore(tmp_path).set(workflow.workflow_id, binding, input_name, approval=approval)


@pytest.fixture(autouse=True)
def as_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run as the user from their own terminal unless a test says otherwise."""
    for name in AGENT_SESSION_ENVS:
        monkeypatch.delenv(name, raising=False)


# The store


def test_bindings_follow_the_desktop_apps_rules() -> None:
    assert clean_binding("Alt+Ctrl+KeyR") == "Ctrl+Alt+KeyR"
    assert clean_binding("Mod+Shift+Digit5") == "Mod+Shift+Digit5"
    assert clean_binding("F9") == "F9"
    assert clean_binding("Shift+F12") == "Shift+F12"
    for bad, words in [
        ("", "Record"),
        ("Ctrl+K Ctrl+O", "single key combination"),
        ("KeyR", "modifier"),
        ("Shift+KeyR", "modifier"),
        ("Hyper+KeyR", "Unknown modifier"),
        ("Ctrl+MediaPlay", "can't be used"),
    ]:
        with pytest.raises(ShortcutError, match=words):
            clean_binding(bad)
    # Mod is Ctrl off a Mac, so the two spellings press the same keys.
    assert binding_identity("Mod+Alt+KeyR", "linux") == binding_identity("Alt+Ctrl+KeyR", "linux")
    assert binding_identity("Mod+Alt+KeyR", "darwin") != binding_identity("Ctrl+Alt+KeyR", "darwin")


def test_store_keeps_one_private_shortcut_per_workflow(tmp_path: Path) -> None:
    store = ShortcutStore(tmp_path)
    first = store.set("wf-one", "Ctrl+Alt+KeyR", None)
    assert len(first.id) == 12 and first.binding == "Ctrl+Alt+KeyR"
    if os.name == "posix":
        assert stat.S_IMODE((tmp_path / SHORTCUTS_FILENAME).stat().st_mode) == 0o600
    # Setting it again changes the key and keeps the ID.
    again = store.set("WF-ONE", "Ctrl+Alt+KeyT", "article")
    assert again.id == first.id and again.workflow_id == "wf-one" and again.input == "article"
    assert [item.binding for item in store.entries()] == ["Ctrl+Alt+KeyT"]

    store.set("wf-two", "Ctrl+Alt+KeyR", None)
    with pytest.raises(ShortcutConflict):
        store.set("wf-three", "Alt+Ctrl+KeyR", None)
    removed = store.remove(first.id)
    assert removed.workflow_id == "wf-one"
    with pytest.raises(ShortcutNotFound):
        store.remove(first.id)
    assert [item.workflow_id for item in store.entries()] == ["wf-two"]


def test_store_has_a_limit_and_skips_damaged_entries(tmp_path: Path) -> None:
    store = ShortcutStore(tmp_path)
    for index in range(MAX_SHORTCUTS):
        modifiers = "Ctrl+Alt" if index < 26 else "Ctrl+Alt+Shift"
        store.set(f"wf-{index}", f"{modifiers}+Key{chr(65 + index % 26)}", None)
    with pytest.raises(ShortcutError, match="at most"):
        store.set("wf-extra", "Ctrl+Meta+KeyZ", None)

    path = tmp_path / SHORTCUTS_FILENAME
    document = json.loads(path.read_text())
    document["shortcuts"] = [
        {"id": "../escape", "workflowId": "x", "binding": "Ctrl+Alt+KeyA"},
        {"id": "abcdefabcdef", "workflowId": "x", "binding": "KeyA"},
        {"id": "abcdefabcdef", "workflowId": "kept", "binding": "Ctrl+Alt+KeyA"},
    ]
    path.write_text(json.dumps(document))
    assert [item.workflow_id for item in store.entries()] == ["kept"]
    path.write_text("{not json")
    with pytest.raises(ShortcutError, match="valid JSON"):
        store.entries()


# What a press does


def test_the_mode_follows_the_workflows_inputs() -> None:
    def ir(*inputs: tuple[str, dict[str, Any], bool, bool]) -> dict[str, Any]:
        return {
            "workflow": {
                "inputs": [
                    {
                        "name": name,
                        "schema": schema,
                        "required": required,
                        "default": {"present": has_default, "value": "x" if has_default else None},
                    }
                    for name, schema, required, has_default in inputs
                ]
            }
        }

    assert plan_target(ir(), None)["mode"] == "run"
    # Inputs with defaults that take no text: the run uses the defaults.
    assert plan_target(ir(("tone", {"enum": ["a", "b"]}, False, True)), None)["mode"] == "run"
    text = plan_target(
        ir(
            ("note", {"type": "string"}, False, True),
            ("body", {"type": "string", "title": "Body"}, True, False),
        ),
        None,
    )
    # The first required text input gets the clipboard unless the user chose another.
    assert (text["mode"], text["input"], text["inputLabel"]) == ("text", "body", "Body")
    chosen = plan_target(
        ir(("note", {"type": "string"}, False, True), ("body", {"type": "string"}, False, True)),
        "body",
    )
    assert chosen["input"] == "body"
    # A password never takes clipboard text, and a required number can't be filled.
    password = plan_target(
        ir(("token", {"type": "string", "format": "password"}, True, False)), None
    )
    assert password["mode"] == "unavailable" and "token" in password["problem"]
    assert plan_target(None, None)["mode"] == "unavailable"


# The API


def test_routes_add_list_and_remove_shortcuts(tmp_path: Path) -> None:
    tidy = _workflow(tmp_path, "Tidy", NO_INPUTS)
    summarize = _workflow(tmp_path, "Summarize", TEXT_INPUT)
    count = _workflow(tmp_path, "Count", NEEDS_NUMBER)

    target = _json(
        _request(
            tmp_path, "GET", f"/api/workflow-shortcuts/target?workflowId={summarize.workflow_id}"
        )
    )
    assert target["shortcut"] is None
    assert target["target"]["mode"] == "text" and target["target"]["input"] == "article"
    assert [item["name"] for item in target["target"]["textInputs"]] == ["article", "note"]

    added = _request(
        tmp_path,
        "POST",
        "/api/workflow-shortcuts",
        body={"workflowId": tidy.workflow_id, "binding": "Ctrl+Alt+KeyT"},
    )
    assert added.status == 200
    shortcut = _json(added)["shortcut"]
    assert shortcut["workflowName"] == "Tidy" and shortcut["target"]["mode"] == "run"
    assert shortcut["target"]["summary"] == "Runs the workflow straight away."

    # The clipboard can't go to note: the required article would be left empty.
    other = _request(
        tmp_path,
        "POST",
        "/api/workflow-shortcuts",
        body={"workflowId": summarize.workflow_id, "binding": "Ctrl+Alt+KeyS", "input": "note"},
    )
    assert other.status == 400 and "article" in _json(other)["error"]
    chosen = _json(
        _request(
            tmp_path,
            "POST",
            "/api/workflow-shortcuts",
            body={
                "workflowId": summarize.workflow_id,
                "binding": "Ctrl+Alt+KeyS",
                "input": "article",
            },
        )
    )["shortcut"]
    assert chosen["input"] == "article" and "Article" in chosen["target"]["summary"]

    clash = _request(
        tmp_path,
        "POST",
        "/api/workflow-shortcuts",
        body={"workflowId": summarize.workflow_id, "binding": "Alt+Ctrl+KeyT"},
    )
    assert clash.status == 409
    refused = _request(
        tmp_path,
        "POST",
        "/api/workflow-shortcuts",
        body={"workflowId": count.workflow_id, "binding": "Ctrl+Alt+KeyC"},
    )
    assert refused.status == 400 and "count" in _json(refused)["error"]
    no_such_input = _request(
        tmp_path,
        "POST",
        "/api/workflow-shortcuts",
        body={"workflowId": summarize.workflow_id, "binding": "Ctrl+Alt+KeyS", "input": "tone"},
    )
    assert no_such_input.status == 400

    listed = _json(_request(tmp_path, "GET", "/api/workflow-shortcuts"))["shortcuts"]
    assert [item["workflowName"] for item in listed] == ["Tidy", "Summarize"]
    assert all("target" not in item and item["available"] for item in listed)
    detailed = _json(_request(tmp_path, "GET", "/api/workflow-shortcuts?details=1"))["shortcuts"]
    assert [item["target"]["mode"] for item in detailed] == ["run", "text"]

    assert _request(tmp_path, "DELETE", f"/api/workflow-shortcuts/{shortcut['id']}").status == 200
    assert _request(tmp_path, "DELETE", f"/api/workflow-shortcuts/{shortcut['id']}").status == 404
    assert _request(tmp_path, "GET", f"/api/workflow-shortcuts/{shortcut['id']}").status == 404
    assert _request(tmp_path, "GET", "/api/workflow-shortcuts", authenticated=False).status == 401


def test_a_press_starts_a_run_the_user_started(tmp_path: Path, monkeypatch: Any) -> None:
    import gofer.ui.api as api

    started: list[dict[str, Any]] = []

    async def fake_run(workflow_id: str, base: Path, **options: Any) -> dict[str, Any]:
        started.append({"workflow_id": workflow_id, **options})
        return {"runId": f"run-{len(started)}", "status": "running"}

    monkeypatch.setattr(api, "run_workflow_payload", fake_run)
    tidy = _workflow(tmp_path, "Tidy", NO_INPUTS)
    summarize = _workflow(tmp_path, "Summarize", TEXT_INPUT)
    plain = _add(tmp_path, tidy, "Ctrl+Alt+KeyT")
    text = _add(tmp_path, summarize, "Ctrl+Alt+KeyS", "article")

    ran = _json(_request(tmp_path, "POST", f"/api/workflow-shortcuts/{plain.id}/run", body={}))
    assert ran["run"]["runId"] == "run-1" and ran["run"]["workflowName"] == "Tidy"
    assert started[0] == {
        "workflow_id": tidy.workflow_id,
        "inputs": {},
        "background": True,
        "started_by": "manual",
    }

    clipped = _request(
        tmp_path, "POST", f"/api/workflow-shortcuts/{text.id}/run", body={"text": "Read this"}
    )
    assert clipped.status == 200 and started[1]["inputs"] == {"article": "Read this"}
    assert (
        _request(tmp_path, "POST", f"/api/workflow-shortcuts/{text.id}/run", body={}).status == 400
    )
    too_long = _request(
        tmp_path,
        "POST",
        f"/api/workflow-shortcuts/{text.id}/run",
        body={"text": "x" * (MAX_TEXT_CHARS + 1)},
    )
    assert too_long.status == 400 and len(started) == 2

    # A workflow that now needs an input a shortcut can't fill says so instead of running.
    # (Approved first: an edited workflow waits for review before anything else.)
    summarize.entrypoint.write_text(NEEDS_NUMBER, encoding="utf-8")
    pending = _request(
        tmp_path, "POST", f"/api/workflow-shortcuts/{text.id}/run", body={"text": "x"}
    )
    assert pending.status == 409 and _json(pending)["code"] == "shortcut_changed"
    approve_shortcut(tmp_path, text.id, actor="user", by="desktop")
    changed = _request(
        tmp_path, "POST", f"/api/workflow-shortcuts/{text.id}/run", body={"text": "x"}
    )
    assert changed.status == 400 and "count" in _json(changed)["error"]
    assert len(started) == 2


def test_agent_tokens_can_only_read_shortcuts() -> None:
    assert agent_request_refusal("GET", "/api/workflow-shortcuts") is None
    for method, path in [
        ("POST", "/api/workflow-shortcuts"),
        ("DELETE", "/api/workflow-shortcuts/abcdefabcdef"),
        ("POST", "/api/workflow-shortcuts/abcdefabcdef/run"),
    ]:
        assert agent_request_refusal(method, path) is not None


# The CLI


def test_cli_lists_and_removes_shortcuts(tmp_path: Path) -> None:
    runner = CliRunner()
    empty = runner.invoke(app, ["rattish", "shortcuts", "list", "--data-dir", str(tmp_path)])
    assert empty.exit_code == 0 and "No workflow shortcuts" in empty.output

    tidy = _workflow(tmp_path, "Tidy", NO_INPUTS)
    shortcut = ShortcutStore(tmp_path).set(tidy.workflow_id, "Ctrl+Alt+KeyT", None)
    listed = runner.invoke(
        app, ["rattish", "shortcuts", "list", "--data-dir", str(tmp_path), "--format", "json"]
    )
    assert listed.exit_code == 0, listed.output
    payload = json.loads(listed.output)
    entry = payload["shortcuts"][0]
    assert entry["id"] == shortcut.id and entry["workflowName"] == "Tidy"
    assert entry["key"] == ("Ctrl+Alt+T" if sys.platform != "darwin" else "Ctrl+Option+T")
    assert entry["target"]["mode"] == "run"
    text = runner.invoke(app, ["rattish", "shortcuts", "list", "--data-dir", str(tmp_path)])
    assert shortcut.id in text.output and "Runs the workflow straight away." in text.output

    missing = runner.invoke(
        app, ["rattish", "shortcuts", "remove", "abcdefabcdef", "--data-dir", str(tmp_path)]
    )
    assert missing.exit_code == 1 and "shortcuts list" in missing.output
    removed = runner.invoke(
        app,
        [
            "rattish",
            "shortcuts",
            "remove",
            shortcut.id,
            "--data-dir",
            str(tmp_path),
            "--format",
            "json",
        ],
    )
    assert removed.exit_code == 0 and json.loads(removed.output)["removed"]["id"] == shortcut.id
    assert ShortcutStore(tmp_path).entries() == []


# Only the approved version runs

SCRIPTED = (
    "Rattish: 1\nWorkflow:\n  name: Tidy files\n"
    "Node tidy:\n  type: python-script\n  script-path: scripts/tidy.py\n"
)


def _scripted(tmp_path: Path) -> Any:
    workflow = _workflow(tmp_path, "Tidy files", SCRIPTED)
    scripts = Path(workflow.project_root) / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "tidy.py").write_text("print('tidy')\n", encoding="utf-8")
    return workflow


def _fake_runs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    import gofer.ui.api as api

    started: list[str] = []

    async def fake_run(workflow_id: str, base: Path, **options: Any) -> dict[str, Any]:
        started.append(workflow_id)
        return {"runId": f"run-{len(started)}", "status": "running"}

    monkeypatch.setattr(api, "run_workflow_payload", fake_run)
    return started


def _press(tmp_path: Path, shortcut_id: str) -> Any:
    return _request(tmp_path, "POST", f"/api/workflow-shortcuts/{shortcut_id}/run", body={})


def _changes(tmp_path: Path, shortcut_id: str) -> dict[str, Any]:
    response = _request(tmp_path, "GET", f"/api/workflow-shortcuts/{shortcut_id}/changes")
    assert response.status == 200, response.text()
    return cast(dict[str, Any], _json(response)["changes"])


def test_an_unchanged_workflow_runs_from_its_shortcut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = _fake_runs(monkeypatch)
    workflow = _scripted(tmp_path)
    added = _request(
        tmp_path,
        "POST",
        "/api/workflow-shortcuts",
        body={"workflowId": workflow.workflow_id, "binding": "Ctrl+Alt+KeyT"},
    )
    assert added.status == 200, added.text()
    shortcut = _json(added)["shortcut"]
    # Adding the shortcut approved the workflow and the script it runs, as they are now.
    assert shortcut["approval"]["state"] == "approved"
    assert shortcut["approvedFingerprint"] == shortcut["approval"]["fingerprint"]
    assert shortcut["approvedBy"] == "desktop"
    snapshot = tmp_path / "workflow-shortcut-approvals" / f"{shortcut['id']}.json"
    assert snapshot.is_file()
    if os.name == "posix":
        assert stat.S_IMODE(snapshot.stat().st_mode) == 0o600
    assert {Path(item["path"]).name for item in json.loads(snapshot.read_text())["files"]} == {
        "workflow.rattish",
        "tidy.py",
    }

    assert _press(tmp_path, shortcut["id"]).status == 200
    assert started == [workflow.workflow_id]
    changes = _changes(tmp_path, shortcut["id"])
    assert changes["state"] == "approved" and changes["files"] == []

    # Removing the shortcut removes its approved version too.
    assert _request(tmp_path, "DELETE", f"/api/workflow-shortcuts/{shortcut['id']}").status == 200
    assert not snapshot.exists()


def test_a_changed_file_waits_for_review_before_the_shortcut_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = _fake_runs(monkeypatch)
    workflow = _scripted(tmp_path)
    shortcut = _add(tmp_path, workflow, "Ctrl+Alt+KeyT")
    script = Path(workflow.project_root) / "scripts" / "tidy.py"
    script.write_text("import shutil\nshutil.rmtree('/')\n", encoding="utf-8")

    refused = _press(tmp_path, shortcut.id)
    assert refused.status == 409 and started == []
    body = _json(refused)
    assert body["code"] == "shortcut_changed" and body["shortcutId"] == shortcut.id
    assert body["error"] == "Tidy files changed since you added its shortcut"

    detail = _json(_request(tmp_path, "GET", f"/api/workflow-shortcuts/{shortcut.id}"))
    assert detail["shortcut"]["approval"]["state"] == "changed"
    changes = _changes(tmp_path, shortcut.id)
    assert changes["state"] == "changed" and changes["workflowName"] == "Tidy files"
    assert [item["path"] for item in changes["files"]] == ["scripts/tidy.py"]
    assert "+shutil.rmtree('/')" in changes["files"][0]["diff"]

    # Changing the key approves nothing: the change keeps waiting.
    rekeyed = _request(
        tmp_path,
        "POST",
        "/api/workflow-shortcuts",
        body={"workflowId": workflow.workflow_id, "binding": "Ctrl+Alt+KeyY"},
    )
    assert rekeyed.status == 200, rekeyed.text()
    assert _json(rekeyed)["shortcut"]["approval"]["state"] == "changed"
    assert _press(tmp_path, shortcut.id).status == 409

    # Approving needs the version that was reviewed.
    approve = f"/api/workflow-shortcuts/{shortcut.id}/approve"
    assert _request(tmp_path, "POST", approve, body={}).status == 400
    script.write_text("print('changed again')\n", encoding="utf-8")
    stale = _request(tmp_path, "POST", approve, body={"fingerprint": changes["fingerprint"]})
    assert stale.status == 409 and _json(stale)["conflict"] is True
    changes = _changes(tmp_path, shortcut.id)
    approved = _request(tmp_path, "POST", approve, body={"fingerprint": changes["fingerprint"]})
    assert approved.status == 200, approved.text()
    assert _json(approved)["shortcut"]["approval"]["state"] == "approved"
    assert _press(tmp_path, shortcut.id).status == 200
    assert started == [workflow.workflow_id]


def test_changes_review_could_not_show_need_a_second_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_runs(monkeypatch)
    workflow = _scripted(tmp_path)
    shortcut = _add(tmp_path, workflow, "Ctrl+Alt+KeyT")
    script = Path(workflow.project_root) / "scripts" / "tidy.py"
    script.write_bytes(b"\x00\xff binary")

    changes = _changes(tmp_path, shortcut.id)
    assert [item["reason"] for item in changes["unseen"]] == ["unreadable"]
    approve = f"/api/workflow-shortcuts/{shortcut.id}/approve"
    refused = _request(tmp_path, "POST", approve, body={"fingerprint": changes["fingerprint"]})
    assert refused.status == 409
    assert [item["path"] for item in _json(refused)["unseen"]] == ["scripts/tidy.py"]
    assert _press(tmp_path, shortcut.id).status == 409

    confirmed = _request(
        tmp_path,
        "POST",
        approve,
        body={"fingerprint": changes["fingerprint"], "acceptUnseen": True},
    )
    assert confirmed.status == 200, confirmed.text()
    assert _press(tmp_path, shortcut.id).status == 200

    # A file too large to keep the text of counts as unseen too.
    script.write_text("x" * (MAX_FILE_TEXT_CHARS + 1), encoding="utf-8")
    assert _changes(tmp_path, shortcut.id)["unseen"][0]["reason"] == "unreadable"


def test_a_studio_save_approves_only_the_workflow_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = _fake_runs(monkeypatch)
    workflow = _scripted(tmp_path)
    shortcut = _add(tmp_path, workflow, "Ctrl+Alt+KeyT")

    def save(extra: str) -> None:
        current = _json(
            _request(tmp_path, "GET", f"/api/workflows/{workflow.workflow_id}/document")
        )["document"]
        saved = _request(
            tmp_path,
            "POST",
            f"/api/workflows/{workflow.workflow_id}/document/save",
            body={
                "source": current["source"] + extra,
                "expectedRevision": current["sourceRevision"],
            },
        )
        assert saved.status == 200, saved.text()

    # The user's own save in Studio runs straight away.
    save("# saved in Studio\n")
    record = ShortcutStore(tmp_path).get(shortcut.id)
    assert record.approved_by == "studio"
    assert record.approved_fingerprint != shortcut.approved_fingerprint
    assert _press(tmp_path, shortcut.id).status == 200 and len(started) == 1

    # A script changed outside Studio stays waiting after the next Studio save.
    script = Path(workflow.project_root) / "scripts" / "tidy.py"
    script.write_text("print('something else')\n", encoding="utf-8")
    save("# saved again\n")
    assert _press(tmp_path, shortcut.id).status == 409 and len(started) == 1
    assert [item["path"] for item in _changes(tmp_path, shortcut.id)["files"]] == [
        "scripts/tidy.py"
    ]


def test_a_shortcut_saved_before_approvals_asks_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = _fake_runs(monkeypatch)
    workflow = _scripted(tmp_path)
    shortcut = ShortcutStore(tmp_path).set(workflow.workflow_id, "Ctrl+Alt+KeyT", None)
    assert shortcut.approved_fingerprint is None
    refused = _press(tmp_path, shortcut.id)
    assert refused.status == 409 and started == []
    assert _json(refused)["error"] == "Tidy files needs your approval before its shortcut runs it"
    changes = _changes(tmp_path, shortcut.id)
    assert changes["state"] == "unapproved"
    assert {item["change"] for item in changes["files"]} == {"added"}
    approved = _request(
        tmp_path,
        "POST",
        f"/api/workflow-shortcuts/{shortcut.id}/approve",
        body={"fingerprint": changes["fingerprint"]},
    )
    assert approved.status == 200
    assert _press(tmp_path, shortcut.id).status == 200


def test_agent_tokens_can_read_but_not_approve_changes() -> None:
    assert agent_request_refusal("GET", "/api/workflow-shortcuts/abcdefabcdef/changes") is None
    assert agent_request_refusal("POST", "/api/workflow-shortcuts/abcdefabcdef/approve")


def test_cli_approve_shows_the_diff_and_is_refused_to_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliRunner()
    workflow = _scripted(tmp_path)
    shortcut = _add(tmp_path, workflow, "Ctrl+Alt+KeyT")
    base = ["rattish", "shortcuts"]
    data = ["--data-dir", str(tmp_path)]

    nothing = runner.invoke(app, [*base, "approve", shortcut.id, *data])
    assert nothing.exit_code == 0 and "Nothing to approve" in nothing.output

    script = Path(workflow.project_root) / "scripts" / "tidy.py"
    script.write_text("print('new')\n", encoding="utf-8")
    listed = runner.invoke(app, [*base, "list", *data])
    assert "Changed since you approved it" in listed.output

    monkeypatch.setenv(AGENT_SESSION_ENVS[0], "1")
    refused = runner.invoke(app, [*base, "approve", shortcut.id, "--yes", *data])
    assert refused.exit_code == 5 and "Only you can approve" in refused.output
    monkeypatch.delenv(AGENT_SESSION_ENVS[0])

    declined = runner.invoke(app, [*base, "approve", shortcut.id, *data], input="n\n")
    assert declined.exit_code == 1 and "+print('new')" in declined.output
    assert ShortcutStore(tmp_path).get(shortcut.id).approved_fingerprint == (
        shortcut.approved_fingerprint
    )
    approved = runner.invoke(app, [*base, "approve", shortcut.id, *data], input="y\n")
    assert approved.exit_code == 0, approved.output
    assert "Approved." in approved.output
    record = ShortcutStore(tmp_path).get(shortcut.id)
    assert record.approved_by == "cli"

    # A change the diff can't show needs --unseen with --yes.
    script.write_bytes(b"\x00\xff binary")
    blocked = runner.invoke(app, [*base, "approve", shortcut.id, "--yes", *data])
    assert blocked.exit_code == 2 and "--unseen" in blocked.output
    accepted = runner.invoke(
        app, [*base, "approve", shortcut.id, "--yes", "--unseen", *data, "--format", "json"]
    )
    assert accepted.exit_code == 0, accepted.output
    assert json.loads(accepted.output)["approved"] is True
