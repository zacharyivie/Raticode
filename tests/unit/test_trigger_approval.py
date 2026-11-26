"""Triggers run only the workflow version the user approved."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from gofer.cli.client import AGENT_SESSION_ENVS
from gofer.cli.main import app
from gofer.core import rem_library
from gofer.core.phone_alerts import PhoneAlertStore
from gofer.rattish.trigger_approval import (
    APPROVAL_SKIP_MESSAGE,
    UNAPPROVED_SKIP_MESSAGE,
    take_snapshot,
)
from gofer.rattish.triggers import (
    USER_ONLY,
    ApprovalConflict,
    TriggerError,
    TriggerStore,
    approval_status,
    approve_trigger,
    hash_token,
    trigger_changes,
)
from gofer.rattish.workspaces import create_registered_workflow
from tests.unit.test_rattish_triggers import START, _engine, _settled, _statuses

runner = CliRunner()

PARENT = """Rattish: 1

Workflow:
  name: Nightly tidy

Node ask:
  type: agent
  provider: codex
  prompt-path: prompts/ask.md
  to: tidy

Node tidy:
  type: python-script
  script-path: scripts/tidy.py
  needs: ask
  to: child

Node child:
  type: workflow
  workflow-path: sub/workflow.rattish
  needs: tidy

Node render:
  type: prompt-file
  output-path: out.md
  template-path: prompts/template.md
"""
CHILD = """Rattish: 1

Workflow:
  name: Child
  interface-version: 1

Node go:
  type: shell-script
  script-path: scripts/child.sh
"""
SIMPLE = """Rattish: 1

Workflow:
  name: Simple

Node hello:
  type: bash-command
  command: echo hello
"""


@pytest.fixture(autouse=True)
def as_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run as the user from their own terminal unless a test says otherwise."""
    for name in AGENT_SESSION_ENVS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "prompts").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "sub").mkdir()
    (root / "workflow.rattish").write_text(PARENT, encoding="utf-8")
    (root / "sub" / "workflow.rattish").write_text(CHILD, encoding="utf-8")
    (root / "prompts" / "ask.md").write_text("Summarize the inbox.\n", encoding="utf-8")
    (root / "prompts" / "template.md").write_text("Report: {{x}}\n", encoding="utf-8")
    (root / "scripts" / "tidy.py").write_text("print('tidy')\n", encoding="utf-8")
    (root / "scripts" / "child.sh").write_text("echo child\n", encoding="utf-8")
    return root


def _cron(store: TriggerStore, source: Path, **options: Any) -> str:
    options.setdefault("now", START - timedelta(seconds=20))
    record, _ = store.create(
        kind="cron",
        source_path=source,
        config={"expression": "* * * * *", "timezone": "UTC"},
        **options,
    )
    return record.id


def _cli(*arguments: str, input_text: str | None = None) -> Any:
    return runner.invoke(app, ["rattish", "triggers", *arguments], input=input_text)


def test_the_snapshot_covers_every_file_the_workflow_runs_as_code(
    data_dir: Path, project: Path
) -> None:
    snapshot = take_snapshot(project / "workflow.rattish", data_dir)
    shown = {Path(item.path).relative_to(project).as_posix(): item.role for item in snapshot.files}
    assert shown == {
        "workflow.rattish": "workflow",
        "prompts/ask.md": "prompt",
        "prompts/template.md": "prompt",
        "scripts/tidy.py": "script",
        "sub/workflow.rattish": "child_workflow",
        # A child's own files count too. Its relative paths start at the caller's project,
        # as they do when it runs.
        "scripts/child.sh": "script",
    }
    assert snapshot.complete and snapshot.workflow_name == "Nightly tidy"
    assert take_snapshot(project / "workflow.rattish", data_dir).fingerprint == snapshot.fingerprint


@pytest.mark.parametrize(
    "edited",
    [
        "workflow.rattish",
        "prompts/ask.md",
        "prompts/template.md",
        "scripts/tidy.py",
        "sub/workflow.rattish",
        "scripts/child.sh",
    ],
)
def test_editing_any_of_them_changes_the_fingerprint(
    data_dir: Path, project: Path, edited: str
) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, project / "workflow.rattish")
    record = store.get(trigger_id)
    assert approval_status(store, record).state == "approved"
    target = project / edited
    target.write_text(target.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    status = approval_status(store, record)
    assert status.state == "changed"
    changes = trigger_changes(store, trigger_id)
    assert [(item["path"], item["change"]) for item in changes["files"]] == [(edited, "changed")]
    assert "+# edited" in changes["files"][0]["diff"]


def test_files_the_workflow_doesnt_run_dont_matter(data_dir: Path, project: Path) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, project / "workflow.rattish")
    (project / "notes.md").write_text("unrelated\n", encoding="utf-8")
    (project / "prompts" / "unused.md").write_text("never named\n", encoding="utf-8")
    assert approval_status(store, store.get(trigger_id)).state == "approved"


def test_a_newly_named_script_shows_as_added_and_the_old_one_as_removed(
    data_dir: Path, project: Path
) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, project / "workflow.rattish")
    (project / "scripts" / "other.py").write_text("import os\n", encoding="utf-8")
    source = project / "workflow.rattish"
    source.write_text(PARENT.replace("scripts/tidy.py", "scripts/other.py"), encoding="utf-8")
    changes = {item["path"]: item for item in trigger_changes(store, trigger_id)["files"]}
    assert changes["workflow.rattish"]["change"] == "changed"
    assert changes["scripts/other.py"]["change"] == "added"
    assert "+import os" in changes["scripts/other.py"]["diff"]
    assert changes["scripts/tidy.py"]["change"] == "removed"


def test_a_workflow_that_no_longer_compiles_still_shows_its_changed_files(
    data_dir: Path, project: Path
) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, project / "workflow.rattish")
    (project / "scripts" / "tidy.py").write_text("print('changed')\n", encoding="utf-8")
    (project / "workflow.rattish").write_text(PARENT + "\nNode broken:\n", encoding="utf-8")
    status = approval_status(store, store.get(trigger_id))
    assert status.state == "changed" and not status.current.complete
    paths = {item["path"] for item in trigger_changes(store, trigger_id)["files"]}
    assert paths == {"workflow.rattish", "scripts/tidy.py"}


def test_a_changed_workflow_is_skipped_logged_once_and_the_phone_told_once(
    data_dir: Path, project: Path
) -> None:
    engine, runs, clock = _engine(data_dir)
    trigger_id = _cron(engine.store, project / "workflow.rattish", label="Nightly")
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _settled(engine, runs, 1)
    assert _statuses(engine.store, trigger_id) == ["passed"]

    (project / "scripts" / "tidy.py").write_text("import os\nos.remove('x')\n", encoding="utf-8")
    for _ in range(3):
        clock.advance(60)
        engine.tick()
    assert len(runs.started) == 1
    latest = engine.store.events(trigger_id=trigger_id)[0]
    assert (latest.status, latest.message, latest.count) == ("skipped", APPROVAL_SKIP_MESSAGE, 3)
    alerts = PhoneAlertStore(data_dir).after(0)["alerts"]
    assert len(alerts) == 1
    assert alerts[0]["title"] == "Workflow changed since you approved it"
    assert alerts[0]["reason"] == "workflow" and alerts[0]["run_id"] == ""
    assert "Nightly on Nightly tidy didn't start" in alerts[0]["body"]

    changes = trigger_changes(engine.store, trigger_id)
    approve_trigger(
        engine.store,
        trigger_id,
        actor="user",
        by="desktop",
        expected_fingerprint=changes["fingerprint"],
    )
    clock.advance(60)
    engine.tick()
    _settled(engine, runs, 2)
    assert len(runs.started) == 2

    # A later change is a new version, so the phone hears about it once more.
    (project / "prompts" / "ask.md").write_text("Delete the inbox.\n", encoding="utf-8")
    clock.advance(60)
    engine.tick()
    assert len(PhoneAlertStore(data_dir).after(0)["alerts"]) == 2


MCP_WORKFLOW = """Rattish: 1

Workflow:
  name: Ask the server

Node call:
  type: mcp-tool
  server: local
  tool: echo
  arguments:
    text: hi
"""


def test_an_mcp_servers_project_script_changed_skips_the_next_start(
    data_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path / "mcp-project"
    (root / "tools").mkdir(parents=True)
    script = root / "tools" / "server.py"
    script.write_text("print('serve')\n", encoding="utf-8")
    (root / "workflow.rattish").write_text(MCP_WORKFLOW, encoding="utf-8")
    rem_library.save_mcp_server(
        data_dir,
        {"name": "local", "type": "stdio", "command": sys.executable, "args": ["tools/server.py"]},
    )
    engine, runs, clock = _engine(data_dir)
    trigger_id = _cron(engine.store, root / "workflow.rattish", label="Ask")
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _settled(engine, runs, 1)
    assert _statuses(engine.store, trigger_id) == ["passed"]

    script.write_text("import os\nos.remove('x')\n", encoding="utf-8")
    clock.advance(60)
    engine.tick()
    assert len(runs.started) == 1
    latest = engine.store.events(trigger_id=trigger_id)[0]
    assert (latest.status, latest.message) == ("skipped", APPROVAL_SKIP_MESSAGE)

    [entry] = trigger_changes(engine.store, trigger_id)["files"]
    assert (entry["path"], entry["roleLabel"], entry["change"]) == (
        "tools/server.py",
        "Script",
        "changed",
    )
    assert "+os.remove('x')" in entry["diff"]


def test_a_queued_start_is_checked_again_when_it_comes_up(data_dir: Path, project: Path) -> None:
    engine, _, _ = _engine(data_dir)
    trigger_id = _cron(engine.store, project / "workflow.rattish", overlap="queue")
    record = engine.store.get(trigger_id)
    engine.become_owner()
    event_id = engine.store.add_event(record, status="queued", at=START, message="Waiting.")
    (project / "scripts" / "tidy.py").write_text("print('changed')\n", encoding="utf-8")
    from gofer.rattish.trigger_service import _Occurrence

    outcome = engine._start(_Occurrence(record), START, event_id=event_id)
    assert outcome.status == "skipped" and outcome.event_id == event_id
    assert engine.store.events(trigger_id=trigger_id)[0].message == APPROVAL_SKIP_MESSAGE


def test_run_edits_without_approval_runs_the_changed_workflow(
    data_dir: Path, project: Path
) -> None:
    engine, runs, clock = _engine(data_dir)
    trigger_id = _cron(engine.store, project / "workflow.rattish", run_unapproved=True)
    assert engine.store.get(trigger_id).run_unapproved is True
    (project / "scripts" / "tidy.py").write_text("print('changed')\n", encoding="utf-8")
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _settled(engine, runs, 1)
    assert _statuses(engine.store, trigger_id) == ["passed"]
    assert PhoneAlertStore(data_dir).after(0)["alerts"] == []


def test_the_switch_is_off_by_default(data_dir: Path, project: Path) -> None:
    store = TriggerStore(data_dir)
    record = store.get(_cron(store, project / "workflow.rattish"))
    assert record.run_unapproved is False
    assert record.to_payload()["runEditsWithoutApproval"] is False


def test_an_agent_cant_approve_its_own_edit(data_dir: Path, project: Path) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, project / "workflow.rattish")
    (project / "scripts" / "tidy.py").write_text("print('agent edit')\n", encoding="utf-8")
    with pytest.raises(TriggerError, match="Only you can approve"):
        approve_trigger(store, trigger_id, actor="rem", by="cli")
    # Changing or turning on the trigger as an agent keeps the approved version.
    store.update(trigger_id, label="Renamed", actor="agent")
    store.update(trigger_id, enabled=False, actor="agent")
    store.update(trigger_id, enabled=True, actor="agent")
    assert approval_status(store, store.get(trigger_id)).state == "changed"
    with pytest.raises(TriggerError, match="Only you can approve"):
        store.update(trigger_id, run_unapproved=True, actor="agent")
    # The scheduler's own changes approve nothing either, and neither do the user's:
    # only Approve this version does, after the change was shown.
    store.update(trigger_id, enabled=False)
    store.update(trigger_id, enabled=True, actor="user")
    assert approval_status(store, store.get(trigger_id)).state == "changed"


def test_a_trigger_an_agent_adds_waits_for_the_users_approval(
    data_dir: Path, project: Path
) -> None:
    engine, runs, clock = _engine(data_dir)
    trigger_id = _cron(engine.store, project / "workflow.rattish", actor="agent")
    with pytest.raises(TriggerError, match="Only you can approve"):
        _cron(engine.store, project / "workflow.rattish", actor="agent", run_unapproved=True)
    assert approval_status(engine.store, engine.store.get(trigger_id)).state == "unapproved"
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    assert runs.started == []
    assert engine.store.events(trigger_id=trigger_id)[0].message == UNAPPROVED_SKIP_MESSAGE
    alert = PhoneAlertStore(data_dir).after(0)["alerts"][0]
    assert alert["title"] == "A trigger is waiting for your approval"
    changes = trigger_changes(engine.store, trigger_id)
    assert {item["change"] for item in changes["files"]} == {"added"}


def test_changing_or_turning_on_a_trigger_approves_nothing_waiting_for_review(
    data_dir: Path, project: Path
) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, project / "workflow.rattish")
    (project / "scripts" / "tidy.py").write_text("print('changed')\n", encoding="utf-8")
    store.update(trigger_id, enabled=False, actor="user")
    store.update(trigger_id, label="Renamed", actor="user")
    store.update(trigger_id, config={"expression": "0 9 * * *"}, actor="user")
    store.update(trigger_id, enabled=True, actor="user")
    status = approval_status(store, store.get(trigger_id))
    assert status.state == "changed"
    assert [item["path"] for item in trigger_changes(store, trigger_id)["files"]] == [
        "scripts/tidy.py"
    ]
    # A brand-new trigger still approves the workflow as it is.
    added = _cron(store, project / "workflow.rattish")
    assert approval_status(store, store.get(added)).state == "approved"


def test_approving_a_version_that_moved_on_is_refused(data_dir: Path, project: Path) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, project / "workflow.rattish")
    (project / "scripts" / "tidy.py").write_text("print('first')\n", encoding="utf-8")
    shown = trigger_changes(store, trigger_id)["fingerprint"]
    (project / "scripts" / "tidy.py").write_text("print('second')\n", encoding="utf-8")
    with pytest.raises(ApprovalConflict):
        approve_trigger(store, trigger_id, actor="user", by="desktop", expected_fingerprint=shown)
    assert approval_status(store, store.get(trigger_id)).state == "changed"


def test_a_missing_workflow_still_fails_visibly_instead_of_waiting(
    data_dir: Path, project: Path
) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, project / "workflow.rattish")
    (project / "workflow.rattish").unlink()
    assert approval_status(store, store.get(trigger_id)).state == "missing"


def test_triggers_saved_before_approvals_keep_running(data_dir: Path, project: Path) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, project / "workflow.rattish")
    # As a version-1 database: no approvals table rows and no switch column.
    with sqlite3.connect(store.path) as db:
        db.execute("DELETE FROM approvals")
        db.execute("ALTER TABLE triggers DROP COLUMN run_unapproved")
        db.execute("PRAGMA user_version = 1")
    reopened = TriggerStore(data_dir)
    record = reopened.get(trigger_id)
    assert record.run_unapproved is False
    status = approval_status(reopened, record)
    assert status.state == "approved" and status.approval is not None
    assert status.approval.approved_by == "upgrade"
    (project / "scripts" / "tidy.py").write_text("print('later')\n", encoding="utf-8")
    assert approval_status(reopened, record).state == "changed"


def test_deleting_a_trigger_removes_its_approval(data_dir: Path, project: Path) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, project / "workflow.rattish")
    store.delete(trigger_id)
    assert store.approval(trigger_id) is None


# Command line ----------------------------------------------------------------------


def test_cli_approve_prints_the_diff_then_approves(data_dir: Path, project: Path) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, project / "workflow.rattish")
    (project / "scripts" / "tidy.py").write_text("print('changed')\n", encoding="utf-8")
    base = ("--data-dir", str(data_dir))

    listed = _cli("list", *base)
    assert "approval=changed" in listed.output
    assert f"Raticode rattish triggers approve {trigger_id}" in listed.output

    declined = _cli("approve", trigger_id, *base, input_text="n\n")
    assert declined.exit_code == 1
    assert "Script: scripts/tidy.py (changed)" in declined.output
    assert "+print('changed')" in declined.output
    assert approval_status(store, store.get(trigger_id)).state == "changed"

    accepted = _cli("approve", trigger_id, *base, input_text="y\n")
    assert accepted.exit_code == 0, accepted.output
    assert accepted.output.index("+print('changed')") < accepted.output.index("Approved.")
    assert approval_status(store, store.get(trigger_id)).state == "approved"
    assert store.approval(trigger_id).approved_by == "cli"  # type: ignore[union-attr]

    nothing = _cli("approve", trigger_id, *base)
    assert "Nothing to approve" in nothing.output


def test_cli_approve_as_json_needs_yes_and_the_reviewed_version(
    data_dir: Path, project: Path
) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, project / "workflow.rattish")
    (project / "prompts" / "ask.md").write_text("Something else.\n", encoding="utf-8")
    base = ("--data-dir", str(data_dir), "--format", "json")
    shown = json.loads(_cli("changes", trigger_id, *base).output)["changes"]
    assert shown["state"] == "changed"
    assert [item["path"] for item in shown["files"]] == ["prompts/ask.md"]

    needs_yes = _cli("approve", trigger_id, *base)
    assert needs_yes.exit_code == 2
    (project / "prompts" / "ask.md").write_text("Changed again.\n", encoding="utf-8")
    stale = _cli("approve", trigger_id, "--yes", "--fingerprint", shown["fingerprint"], *base)
    assert stale.exit_code == 4
    assert json.loads(stale.output)["conflict"] is True
    approved = _cli("approve", trigger_id, "--yes", *base)
    assert approved.exit_code == 0, approved.output
    assert json.loads(approved.output)["trigger"]["approval"]["state"] == "approved"


def test_cli_in_an_agent_turn_approves_nothing(
    data_dir: Path, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, project / "workflow.rattish")
    (project / "scripts" / "tidy.py").write_text("print('agent edit')\n", encoding="utf-8")
    monkeypatch.setenv("RATICODE_AGENT_SESSION", "1")
    base = ("--data-dir", str(data_dir))

    refused = _cli("approve", trigger_id, "--yes", *base)
    assert refused.exit_code == 5
    assert USER_ONLY in refused.output
    assert _cli("update", trigger_id, "--label", "x", *base).exit_code == 0
    assert _cli("enable", trigger_id, *base).exit_code == 0
    assert approval_status(store, store.get(trigger_id)).state == "changed"
    switched = _cli("update", trigger_id, "--run-edits-without-approval", *base)
    # Exit 5, like every other decision only the user may make.
    assert switched.exit_code == 5 and "Only you can approve" in switched.output
    added = _cli(
        "add", "cron", str(project / "workflow.rattish"), "--cron", "0 9 * * *", *base
    )  # fmt: skip
    assert added.exit_code == 0
    assert "Waiting for you to approve this workflow version." in added.output


def test_cli_in_an_agent_turn_cant_take_a_webhook_token_or_loosen_limits(
    data_dir: Path, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = TriggerStore(data_dir)
    record, token = store.create(kind="webhook", source_path=project / "workflow.rattish")
    store.set_webhook_port(18_765)
    base = ("--data-dir", str(data_dir))
    _cli("budget", "--max-tokens", "1000", *base)
    _cli("pause", "--all", *base)
    budget_before = _cli("budget", "--format", "json", *base).output
    assert "1000" in budget_before
    monkeypatch.setenv("RATICODE_AGENT_SESSION", "1")

    rotated = _cli("rotate-token", record.id, "--format", "json", *base)
    assert rotated.exit_code == 5
    assert json.loads(rotated.output)["code"] == "user_only"
    assert "rtw_" not in rotated.output
    # The user's webhook token still works: nothing was replaced.
    assert token and store.get(record.id).token_hash == hash_token(token)
    for refused in (
        ("webhook-port", "2222"),
        ("budget", "--max-tokens", "0"),
        ("resume", "--all"),
    ):
        result = _cli(*refused, *base)
        assert result.exit_code == 5, (refused, result.output)
        assert "Only you can" in result.output
    assert store.webhook_port() == 18_765
    assert store.pause_state().paused
    assert _cli("budget", "--format", "json", *base).output == budget_before
    # Reading them, and pausing, still work.
    assert _cli("webhook-port", *base).output.strip() == "18765"
    assert _cli("pause", "--all", *base).exit_code == 0


def test_cli_in_an_agent_turn_can_lower_trigger_limits_but_not_raise_or_remove_them(
    data_dir: Path, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, project / "workflow.rattish")
    limits = {"daily_max_cost_usd": 5, "daily_max_tokens": 50_000, "max_run_seconds": 3600}
    store.update(trigger_id, **limits, actor="user")
    monkeypatch.setenv("RATICODE_AGENT_SESSION", "1")
    base = ("--data-dir", str(data_dir))

    for refused in (
        ("--daily-max-cost-usd", "0"),
        ("--daily-max-tokens", "0"),
        ("--max-run-time", "none"),
        ("--daily-max-cost-usd", "6"),
        ("--daily-max-tokens", "60000"),
        ("--max-run-time", "2h"),
    ):
        result = _cli("update", trigger_id, *refused, *base)
        assert result.exit_code == 5, (refused, result.output)
        assert "an agent turn can only lower them" in result.output
    as_json = _cli("update", trigger_id, "--max-run-time", "none", "--format", "json", *base)
    assert as_json.exit_code == 5
    assert json.loads(as_json.output)["code"] == "user_only"
    kept = store.get(trigger_id)
    assert (kept.daily_max_cost_usd, kept.daily_max_tokens, kept.max_run_seconds) == (
        5,
        50_000,
        3600,
    )

    lowered = _cli(
        "update", trigger_id, "--daily-max-cost-usd", "2.5", "--daily-max-tokens", "50000",
        "--max-run-time", "30m", *base,
    )  # fmt: skip
    assert lowered.exit_code == 0, lowered.output
    saved = store.get(trigger_id)
    assert (saved.daily_max_cost_usd, saved.daily_max_tokens, saved.max_run_seconds) == (
        2.5,
        50_000,
        1800,
    )

    # An agent-added trigger keeps the default Time limit or a lower one.
    workflow = str(project / "workflow.rattish")
    for refused in (("--max-run-time", "none"), ("--max-run-time", "7h")):
        result = _cli("add", "cron", workflow, "--cron", "0 9 * * *", *refused, *base)
        assert result.exit_code == 5, (refused, result.output)
    assert len(store.list_triggers()) == 1
    added = _cli("add", "cron", workflow, "--cron", "0 9 * * *", "--max-run-time", "1h", *base)
    assert added.exit_code == 0, added.output
    assert sorted(item.max_run_seconds or 0 for item in store.list_triggers()) == [1800, 3600]

    # The user can still raise and remove them.
    monkeypatch.delenv("RATICODE_AGENT_SESSION")
    removed = _cli(
        "update", trigger_id, "--daily-max-cost-usd", "0", "--max-run-time", "none", *base
    )  # fmt: skip
    assert removed.exit_code == 0, removed.output
    assert store.get(trigger_id).daily_max_cost_usd is None
    assert store.get(trigger_id).max_run_seconds is None


def test_store_limits_check_applies_to_every_non_user_actor(data_dir: Path, project: Path) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, project / "workflow.rattish")
    for actor in ("rem", "agent", None):
        with pytest.raises(TriggerError, match="can only lower them"):
            store.update(trigger_id, max_run_seconds=None, actor=actor)
    # A trigger with no limit can get one, and other changes leave limits alone.
    store.update(trigger_id, daily_max_tokens=100, label="renamed", actor="rem")
    store.update(trigger_id, enabled=False)
    assert store.get(trigger_id).daily_max_tokens == 100


def test_cli_sets_run_edits_without_approval(data_dir: Path, project: Path) -> None:
    base = ("--data-dir", str(data_dir), "--format", "json")
    added = _cli(
        "add", "webhook", str(project / "workflow.rattish"), "--run-edits-without-approval", *base
    )  # fmt: skip
    trigger = json.loads(added.output)["trigger"]
    assert trigger["runEditsWithoutApproval"] is True
    updated = _cli("update", trigger["id"], "--require-approval", *base)
    assert json.loads(updated.output)["trigger"]["runEditsWithoutApproval"] is False


# Desktop API -----------------------------------------------------------------------


def _request(*args: Any, **kwargs: Any) -> Any:
    from tests.unit.test_ui_server import _request as request

    return request(*args, **kwargs)


def _registered(tmp_path: Path, project: Path) -> str:
    return create_registered_workflow(project, name="Nightly", registry_dir=tmp_path).workflow_id


def test_triggers_section_reviews_and_approves_a_change(tmp_path: Path) -> None:
    project = tmp_path / "trigger-project"
    project.mkdir()
    workflow_id = _registered(tmp_path, project)
    source = project / ".raticode" / "workflows" / workflow_id / "workflow.rattish"
    if not source.exists():
        source = next(project.rglob("workflow.rattish"))
    created = _request(
        tmp_path,
        "POST",
        "/api/rattish/triggers",
        body={"workflowId": workflow_id, "kind": "webhook"},
    )
    trigger = json.loads(created.body)["trigger"]
    assert trigger["approval"]["state"] == "approved"
    assert trigger["approval"]["approvedBy"] == "desktop"
    assert trigger["runEditsWithoutApproval"] is False

    source.write_text(source.read_text(encoding="utf-8") + "\n# edit\n", encoding="utf-8")
    listed = json.loads(
        _request(tmp_path, "GET", f"/api/rattish/triggers?workflowId={workflow_id}").body
    )
    assert listed["triggers"][0]["approval"]["state"] == "changed"
    changes = json.loads(
        _request(tmp_path, "GET", f"/api/rattish/triggers/{trigger['id']}/changes").body
    )["changes"]
    assert changes["files"][0]["change"] == "changed"
    assert "+# edit" in changes["files"][0]["diff"]

    missing = _request(tmp_path, "POST", f"/api/rattish/triggers/{trigger['id']}/approve", body={})
    assert missing.status == 400
    source.write_text(source.read_text(encoding="utf-8") + "# again\n", encoding="utf-8")
    stale = _request(
        tmp_path,
        "POST",
        f"/api/rattish/triggers/{trigger['id']}/approve",
        body={"fingerprint": changes["fingerprint"]},
    )
    assert stale.status == 409
    fresh = json.loads(
        _request(tmp_path, "GET", f"/api/rattish/triggers/{trigger['id']}/changes").body
    )["changes"]
    approved = _request(
        tmp_path,
        "POST",
        f"/api/rattish/triggers/{trigger['id']}/approve",
        body={"fingerprint": fresh["fingerprint"]},
    )
    assert approved.status == 200, approved.text()
    assert json.loads(approved.body)["trigger"]["approval"]["state"] == "approved"

    switched = _request(
        tmp_path,
        "POST",
        f"/api/rattish/triggers/{trigger['id']}",
        body={"runEditsWithoutApproval": True},
    )
    assert json.loads(switched.body)["trigger"]["runEditsWithoutApproval"] is True


def test_saving_in_studio_approves_the_saved_version(tmp_path: Path) -> None:
    project = tmp_path / "trigger-project"
    project.mkdir()
    workflow_id = _registered(tmp_path, project)
    store = TriggerStore(tmp_path)
    opened = cast(
        dict[str, Any],
        _request(tmp_path, "GET", f"/api/workflows/{workflow_id}/document").json(),
    )["document"]
    entry = Path(opened.get("entrypoint") or opened.get("sourcePath") or "")
    if not entry.is_file():
        entry = next(project.rglob("workflow.rattish"))
    record, _ = store.create(kind="webhook", source_path=entry)
    entry.write_text(opened["source"] + "\n# outside edit\n", encoding="utf-8")
    assert approval_status(store, store.get(record.id)).state == "changed"

    current = cast(
        dict[str, Any],
        _request(tmp_path, "GET", f"/api/workflows/{workflow_id}/document").json(),
    )["document"]
    saved = _request(
        tmp_path,
        "POST",
        f"/api/workflows/{workflow_id}/document/save",
        body={
            "source": current["source"] + "# saved in Studio\n",
            "expectedRevision": current["sourceRevision"],
        },
    )
    assert saved.status == 200, saved.text()
    status = approval_status(store, store.get(record.id))
    assert status.state == "approved"
    assert status.approval is not None and status.approval.approved_by == "studio"


# Approvals cover only what was shown -------------------------------------------------


def test_a_studio_save_approves_only_the_workflow_file_it_wrote(
    data_dir: Path, project: Path
) -> None:
    from gofer.rattish.triggers import approve_source_triggers

    store = TriggerStore(data_dir)
    source = project / "workflow.rattish"
    trigger_id = _cron(store, source)
    # A prompt changes outside Studio, then the user saves the workflow in Studio.
    (project / "prompts" / "ask.md").write_text("Delete the inbox.\n", encoding="utf-8")
    source.write_text(PARENT.replace("Nightly tidy", "Nightly tidy v2"), encoding="utf-8")
    assert approve_source_triggers(store, source, by="studio") == 1

    status = approval_status(store, store.get(trigger_id))
    assert status.state == "changed"
    assert status.approval is not None and status.approval.approved_by == "studio"
    changes = trigger_changes(store, trigger_id)
    assert [(item["path"], item["change"]) for item in changes["files"]] == [
        ("prompts/ask.md", "changed")
    ]
    assert "+Delete the inbox." in changes["files"][0]["diff"]

    # Once the prompt is approved, a later save approves the workflow file again.
    approve_trigger(store, trigger_id, actor="user", by="desktop")
    source.write_text(PARENT.replace("Nightly tidy", "Nightly tidy v3"), encoding="utf-8")
    approve_source_triggers(store, source, by="studio")
    assert approval_status(store, store.get(trigger_id)).state == "approved"


def test_a_studio_save_leaves_a_newly_named_file_unapproved(data_dir: Path, project: Path) -> None:
    from gofer.rattish.triggers import approve_source_triggers

    store = TriggerStore(data_dir)
    source = project / "workflow.rattish"
    trigger_id = _cron(store, source)
    (project / "scripts" / "new.py").write_text("print('new')\n", encoding="utf-8")
    source.write_text(PARENT.replace("scripts/tidy.py", "scripts/new.py"), encoding="utf-8")
    approve_source_triggers(store, source, by="studio")

    changes = trigger_changes(store, trigger_id)
    # The old script dropped out with the saved text; the new one waits for review.
    assert [(item["path"], item["change"]) for item in changes["files"]] == [
        ("scripts/new.py", "added")
    ]


def test_a_studio_save_gives_an_agent_added_trigger_no_approval(
    data_dir: Path, project: Path
) -> None:
    from gofer.rattish.triggers import approve_source_triggers

    store = TriggerStore(data_dir)
    source = project / "workflow.rattish"
    trigger_id = _cron(store, source, actor="agent")
    source.write_text(PARENT + "\n", encoding="utf-8")
    assert approve_source_triggers(store, source, by="studio") == 0
    assert approval_status(store, store.get(trigger_id)).state == "unapproved"


def _long_prompt(project: Path, lines: int, word: str = "tidy") -> None:
    text = "".join(f"Step {index}: {word} one more thing.\n" for index in range(lines))
    (project / "prompts" / "ask.md").write_text(text, encoding="utf-8")


def test_approving_a_cut_diff_needs_a_second_confirmation(data_dir: Path, project: Path) -> None:
    from gofer.rattish.trigger_approval import MAX_DIFF_LINES
    from gofer.rattish.triggers import UnseenChanges

    store = TriggerStore(data_dir)
    trigger_id = _cron(store, project / "workflow.rattish")
    _long_prompt(project, MAX_DIFF_LINES + 50)
    (project / "scripts" / "tidy.py").write_text("print('also changed')\n", encoding="utf-8")
    changes = trigger_changes(store, trigger_id)
    assert changes["truncated"] is True
    unseen = {item["path"]: item for item in changes["unseen"]}
    # The prompt's diff was cut, and the script after it got none of the shared budget.
    assert set(unseen) == {"prompts/ask.md", "scripts/tidy.py"}
    assert unseen["prompts/ask.md"]["reason"] == "cut"
    assert unseen["prompts/ask.md"]["absolutePath"] == str(project / "prompts" / "ask.md")

    with pytest.raises(UnseenChanges) as refused:
        approve_trigger(
            store,
            trigger_id,
            actor="user",
            by="desktop",
            expected_fingerprint=changes["fingerprint"],
        )
    assert "prompts/ask.md (diff cut short)" in str(refused.value)
    assert {item["path"] for item in refused.value.files} == set(unseen)
    assert approval_status(store, store.get(trigger_id)).state == "changed"

    approve_trigger(
        store,
        trigger_id,
        actor="user",
        by="desktop",
        expected_fingerprint=changes["fingerprint"],
        accept_unseen=True,
    )
    assert approval_status(store, store.get(trigger_id)).state == "approved"


@pytest.mark.parametrize("kind", ["binary", "large"])
def test_approving_a_file_that_cant_be_shown_needs_a_second_confirmation(
    data_dir: Path, project: Path, kind: str
) -> None:
    from gofer.rattish.trigger_approval import MAX_FILE_TEXT_CHARS
    from gofer.rattish.triggers import UnseenChanges

    store = TriggerStore(data_dir)
    trigger_id = _cron(store, project / "workflow.rattish")
    script = project / "scripts" / "tidy.py"
    if kind == "binary":
        script.write_bytes(b"\xff\xfe\x00binary")
    else:
        script.write_text("#" * (MAX_FILE_TEXT_CHARS + 1), encoding="utf-8")
    changes = trigger_changes(store, trigger_id)
    assert changes["unseen"] == [
        {
            "path": "scripts/tidy.py",
            "absolutePath": str(script),
            "roleLabel": "Script",
            "reason": "unreadable",
        }
    ]
    with pytest.raises(UnseenChanges, match="binary or too large to show"):
        approve_trigger(store, trigger_id, actor="user", by="desktop")
    approve_trigger(store, trigger_id, actor="user", by="desktop", accept_unseen=True)
    assert approval_status(store, store.get(trigger_id)).state == "approved"


def test_a_removed_or_missing_file_needs_no_second_confirmation(
    data_dir: Path, project: Path
) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, project / "workflow.rattish")
    (project / "scripts" / "tidy.py").unlink()
    changes = trigger_changes(store, trigger_id)
    assert [item["change"] for item in changes["files"]] == ["missing"]
    assert changes["unseen"] == []
    approve_trigger(store, trigger_id, actor="user", by="desktop")
    assert approval_status(store, store.get(trigger_id)).state == "approved"


def test_cli_edits_and_enable_print_the_change_and_ask(data_dir: Path, project: Path) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, project / "workflow.rattish")
    (project / "scripts" / "tidy.py").write_text("print('changed')\n", encoding="utf-8")
    base = ("--data-dir", str(data_dir))

    # Not a terminal and no --yes: the edit is saved, the change keeps waiting.
    renamed = _cli("update", trigger_id, "--label", "Renamed", *base)
    assert renamed.exit_code == 0, renamed.output
    assert store.get(trigger_id).label == "Renamed"
    assert "Workflow changed since you approved it." in renamed.output
    assert "Add --yes" in renamed.output
    assert approval_status(store, store.get(trigger_id)).state == "changed"

    disabled = _cli("disable", trigger_id, *base)
    assert disabled.exit_code == 0 and "Add --yes" not in disabled.output

    as_json = json.loads(_cli("enable", trigger_id, "--format", "json", *base).output)
    assert as_json["trigger"]["approval"]["state"] == "changed"
    assert "approved" not in as_json

    approved = _cli("enable", trigger_id, "--yes", *base)
    assert approved.exit_code == 0, approved.output
    assert approved.output.index("+print('changed')") < approved.output.index("Approved.")
    status = approval_status(store, store.get(trigger_id))
    assert status.state == "approved"
    assert status.approval is not None and status.approval.approved_by == "cli"


def test_cli_edits_ask_at_a_terminal(
    data_dir: Path, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.cli.commands import rattish_triggers

    store = TriggerStore(data_dir)
    trigger_id = _cron(store, project / "workflow.rattish")
    (project / "scripts" / "tidy.py").write_text("print('changed')\n", encoding="utf-8")
    monkeypatch.setattr(rattish_triggers, "_interactive", lambda: True)
    base = ("--data-dir", str(data_dir))

    declined = _cli("update", trigger_id, "--label", "Renamed", *base, input_text="n\n")
    assert declined.exit_code == 0, declined.output
    assert "+print('changed')" in declined.output
    assert "Approve this version for the trigger?" in declined.output
    assert approval_status(store, store.get(trigger_id)).state == "changed"

    accepted = _cli("update", trigger_id, "--label", "Again", *base, input_text="y\n")
    assert accepted.exit_code == 0, accepted.output
    assert approval_status(store, store.get(trigger_id)).state == "approved"


def test_cli_approve_needs_unseen_for_changes_it_couldnt_show(
    data_dir: Path, project: Path
) -> None:
    from gofer.rattish.trigger_approval import MAX_DIFF_LINES

    store = TriggerStore(data_dir)
    trigger_id = _cron(store, project / "workflow.rattish")
    _long_prompt(project, MAX_DIFF_LINES + 50)
    base = ("--data-dir", str(data_dir))
    prompt = str(project / "prompts" / "ask.md")

    refused = _cli("approve", trigger_id, "--yes", *base)
    assert refused.exit_code == 2
    assert f"{prompt} (diff cut short)" in refused.output
    assert "--unseen" in refused.output
    assert approval_status(store, store.get(trigger_id)).state == "changed"

    as_json = _cli("approve", trigger_id, "--yes", "--format", "json", *base)
    assert as_json.exit_code == 2
    assert [item["path"] for item in json.loads(as_json.output)["unseen"]] == ["prompts/ask.md"]

    # At the prompt the second question names the files; no keeps it waiting.
    second_no = _cli("approve", trigger_id, *base, input_text="y\nn\n")
    assert second_no.exit_code == 2
    assert "Approve them without seeing all of their changes?" in second_no.output
    assert approval_status(store, store.get(trigger_id)).state == "changed"

    second_yes = _cli("approve", trigger_id, *base, input_text="y\ny\n")
    assert second_yes.exit_code == 0, second_yes.output
    assert approval_status(store, store.get(trigger_id)).state == "approved"

    _long_prompt(project, MAX_DIFF_LINES + 50, "sweep")
    flagged = _cli("approve", trigger_id, "--yes", "--unseen", *base)
    assert flagged.exit_code == 0, flagged.output
    assert approval_status(store, store.get(trigger_id)).state == "approved"

    # Editing a trigger with --yes alone doesn't approve them either.
    _long_prompt(project, MAX_DIFF_LINES + 50, "dust")
    edited = _cli("enable", trigger_id, "--yes", *base)
    assert edited.exit_code == 0
    assert "--unseen" in edited.output
    assert approval_status(store, store.get(trigger_id)).state == "changed"
    _cli("enable", trigger_id, "--yes", "--unseen", *base)
    assert approval_status(store, store.get(trigger_id)).state == "approved"


def test_desktop_edits_approve_nothing_and_unseen_changes_ask_again(tmp_path: Path) -> None:
    from gofer.rattish.trigger_approval import MAX_DIFF_LINES

    project = tmp_path / "trigger-project"
    project.mkdir()
    workflow_id = _registered(tmp_path, project)
    source = next(project.rglob("workflow.rattish"))
    created = json.loads(
        _request(
            tmp_path,
            "POST",
            "/api/rattish/triggers",
            body={"workflowId": workflow_id, "kind": "webhook"},
        ).body
    )["trigger"]
    original = source.read_text(encoding="utf-8")
    long_tail = "".join(f"# line {index}\n" for index in range(MAX_DIFF_LINES + 10))
    source.write_text(original + long_tail, encoding="utf-8")

    edited = _request(
        tmp_path, "POST", f"/api/rattish/triggers/{created['id']}", body={"label": "Renamed"}
    )
    assert json.loads(edited.body)["trigger"]["approval"]["state"] == "changed"
    turned_on = _request(
        tmp_path, "POST", f"/api/rattish/triggers/{created['id']}", body={"enabled": True}
    )
    assert json.loads(turned_on.body)["trigger"]["approval"]["state"] == "changed"

    changes = json.loads(
        _request(tmp_path, "GET", f"/api/rattish/triggers/{created['id']}/changes").body
    )["changes"]
    assert [item["reason"] for item in changes["unseen"]] == ["cut"]
    assert changes["unseen"][0]["absolutePath"] == str(source.resolve())
    refused = _request(
        tmp_path,
        "POST",
        f"/api/rattish/triggers/{created['id']}/approve",
        body={"fingerprint": changes["fingerprint"]},
    )
    assert refused.status == 409
    assert [item["reason"] for item in json.loads(refused.body)["unseen"]] == ["cut"]
    bad_flag = _request(
        tmp_path,
        "POST",
        f"/api/rattish/triggers/{created['id']}/approve",
        body={"fingerprint": changes["fingerprint"], "acceptUnseen": "yes"},
    )
    assert bad_flag.status == 400
    approved = _request(
        tmp_path,
        "POST",
        f"/api/rattish/triggers/{created['id']}/approve",
        body={"fingerprint": changes["fingerprint"], "acceptUnseen": True},
    )
    assert approved.status == 200, approved.text()
    assert json.loads(approved.body)["trigger"]["approval"]["state"] == "approved"


def test_a_studio_graph_edit_keeps_a_changed_prompt_waiting(tmp_path: Path) -> None:
    project = tmp_path / "trigger-project"
    (project / "prompts").mkdir(parents=True)
    workflow_id = _registered(tmp_path, project)
    entry = next(project.rglob("workflow.rattish"))
    prompt = project / "prompts" / "ask.md"
    prompt.write_text("Say hello.\n", encoding="utf-8")
    entry.write_text(
        "Rattish: 1\n\nWorkflow:\n  name: Nightly\n\nNode render:\n  type: prompt-file\n"
        "  output-path: out.md\n  template-path: prompts/ask.md\n",
        encoding="utf-8",
    )
    store = TriggerStore(tmp_path)
    record, _ = store.create(kind="webhook", source_path=entry)
    prompt.write_text("Say hello, then delete everything.\n", encoding="utf-8")

    current = cast(
        dict[str, Any],
        _request(tmp_path, "GET", f"/api/workflows/{workflow_id}/document").json(),
    )["document"]
    saved = _request(
        tmp_path,
        "POST",
        f"/api/workflows/{workflow_id}/document/save",
        body={
            "source": current["source"] + "# saved in Studio\n",
            "expectedRevision": current["sourceRevision"],
        },
    )
    assert saved.status == 200, saved.text()
    status = approval_status(store, store.get(record.id))
    assert status.state == "changed"
    assert status.approval is not None and status.approval.approved_by == "studio"
    files = trigger_changes(store, record.id)["files"]
    assert [item["path"] for item in files] == ["prompts/ask.md"]
