"""The approve flow shared by `rattish triggers approve` and `rattish shortcuts approve`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gofer.cli.approval import changes_text
from gofer.cli.client import AGENT_SESSION_ENVS
from gofer.cli.main import app
from gofer.rattish.trigger_approval import take_snapshot
from gofer.rattish.triggers import USER_ONLY as TRIGGER_USER_ONLY
from gofer.rattish.triggers import TriggerStore, approval_status
from gofer.rattish.workflow_shortcuts import USER_ONLY as SHORTCUT_USER_ONLY
from gofer.rattish.workflow_shortcuts import ShortcutStore
from gofer.rattish.workspaces import create_registered_workflow

runner = CliRunner()

SCRIPTED = (
    "Rattish: 1\nWorkflow:\n  name: Tidy files\n"
    "Node tidy:\n  type: python-script\n  script-path: scripts/tidy.py\n"
)


@pytest.fixture(autouse=True)
def as_user(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in AGENT_SESSION_ENVS:
        monkeypatch.delenv(name, raising=False)


def _project(tmp_path: Path) -> Any:
    project = tmp_path / "project"
    project.mkdir()
    workflow = create_registered_workflow(project, "Tidy files", registry_dir=tmp_path)
    workflow.entrypoint.write_text(SCRIPTED, encoding="utf-8")
    (project / "scripts").mkdir(exist_ok=True)
    (project / "scripts" / "tidy.py").write_text("print('tidy')\n", encoding="utf-8")
    return workflow


def _edit(workflow: Any) -> None:
    script = Path(workflow.project_root) / "scripts" / "tidy.py"
    script.write_text("print('agent edit')\n", encoding="utf-8")


def _trigger(tmp_path: Path, workflow: Any) -> tuple[TriggerStore, str]:
    store = TriggerStore(tmp_path)
    record, _ = store.create(
        kind="cron",
        source_path=workflow.entrypoint,
        config={"expression": "0 9 * * *", "timezone": "UTC"},
    )
    return store, record.id


def _shortcut(tmp_path: Path, workflow: Any) -> Any:
    approval = take_snapshot(workflow.entrypoint, tmp_path)
    return ShortcutStore(tmp_path).set(
        workflow.workflow_id, "Ctrl+Alt+KeyT", None, approval=approval
    )


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_an_agent_turn_cant_approve_a_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output_format: str
) -> None:
    workflow = _project(tmp_path)
    store, trigger_id = _trigger(tmp_path, workflow)
    _edit(workflow)
    monkeypatch.setenv(AGENT_SESSION_ENVS[0], "1")
    data = ["--data-dir", str(tmp_path), "--format", output_format]

    refused = runner.invoke(
        app, ["rattish", "triggers", "approve", trigger_id, "--yes", "--unseen", *data]
    )
    assert refused.exit_code == 5
    if output_format == "json":
        assert json.loads(refused.output) == {
            "ok": False,
            "error": TRIGGER_USER_ONLY,
            "code": "user_only",
        }
    else:
        assert TRIGGER_USER_ONLY in refused.output
        # Refused before reading anything: no diff is printed.
        assert "agent edit" not in refused.output
    assert approval_status(store, store.get(trigger_id)).state == "changed"

    # The refusal comes before the trigger is looked up, so a wrong ID exits 5 too.
    unknown = runner.invoke(app, ["rattish", "triggers", "approve", "nope", "--yes", *data])
    assert unknown.exit_code == 5


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_an_agent_turn_cant_approve_a_shortcut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output_format: str
) -> None:
    workflow = _project(tmp_path)
    shortcut = _shortcut(tmp_path, workflow)
    _edit(workflow)
    monkeypatch.setenv(AGENT_SESSION_ENVS[0], "1")
    data = ["--data-dir", str(tmp_path), "--format", output_format]

    refused = runner.invoke(
        app, ["rattish", "shortcuts", "approve", shortcut.id, "--yes", "--unseen", *data]
    )
    assert refused.exit_code == 5
    if output_format == "json":
        assert json.loads(refused.output) == {
            "ok": False,
            "error": SHORTCUT_USER_ONLY,
            "code": "user_only",
        }
    else:
        assert SHORTCUT_USER_ONLY in refused.output
        assert "agent edit" not in refused.output
    record = ShortcutStore(tmp_path).get(shortcut.id)
    assert record.approved_fingerprint == shortcut.approved_fingerprint

    unknown = runner.invoke(app, ["rattish", "shortcuts", "approve", "nope", "--yes", *data])
    assert unknown.exit_code == 5


def test_both_approve_commands_ask_for_yes_in_json(tmp_path: Path) -> None:
    workflow = _project(tmp_path)
    store, trigger_id = _trigger(tmp_path, workflow)
    shortcut = _shortcut(tmp_path, workflow)
    _edit(workflow)
    data = ["--data-dir", str(tmp_path), "--format", "json"]

    for group, item_id in (("triggers", trigger_id), ("shortcuts", shortcut.id)):
        needs_yes = runner.invoke(app, ["rattish", group, "approve", item_id, *data])
        assert needs_yes.exit_code == 2, needs_yes.output
        payload = json.loads(needs_yes.output)
        assert payload["ok"] is False
        assert payload["error"] == "Add --yes to approve without a prompt."
        assert payload["changes"]["state"] == "changed"

    approved = runner.invoke(app, ["rattish", "triggers", "approve", trigger_id, "--yes", *data])
    assert approved.exit_code == 0, approved.output
    assert json.loads(approved.output)["trigger"]["id"] == trigger_id
    approved = runner.invoke(app, ["rattish", "shortcuts", "approve", shortcut.id, "--yes", *data])
    assert approved.exit_code == 0, approved.output
    assert json.loads(approved.output)["shortcut"]["id"] == shortcut.id
    assert approval_status(store, store.get(trigger_id)).state == "approved"


def test_the_changed_during_run_line_shows_only_when_marked() -> None:
    words: dict[str, Any] = {
        "noun": "shortcut",
        "states": {"changed": "Changed."},
        "missing": "Missing.",
    }
    changes: dict[str, Any] = {"state": "changed", "files": []}
    plain = changes_text(changes, **words)
    assert "changed during a triggered run" not in plain
    assert "No file differs; the workflow uses a different set of files now." in plain

    marked = changes_text({**changes, "changedDuringRun": {"path": "tidy.py"}}, **words)
    assert "tidy.py changed during a triggered run" in marked
    assert "No file differs from the approved version now." in marked

    assert changes_text({"state": "approved"}, **words) == (
        "This shortcut runs the workflow as it is now. Nothing to approve."
    )
    assert changes_text({"state": "missing"}, **words) == "Missing."


@pytest.mark.parametrize(
    "command", [["triggers", "list"], ["shortcuts", "list"], ["secrets", "list"], ["cache", "show"]]
)
def test_every_rattish_group_names_an_unsupported_format_the_same_way(
    tmp_path: Path, command: list[str]
) -> None:
    bad = runner.invoke(app, ["rattish", *command, "--data-dir", str(tmp_path), "--format", "xml"])
    assert bad.exit_code == 2
    assert "Unsupported format. Use 'text' or 'json'." in bad.output
