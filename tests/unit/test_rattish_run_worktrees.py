"""Runs of a ``workspace: worktree`` workflow: own worktree, then land or discard."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from gofer.rattish import run_worktrees
from gofer.rattish.artifacts import compile_rattish_file
from gofer.rattish.diagnostics import RattishError
from gofer.rattish.preflight import run_preflight
from gofer.rattish.run_service import RattishRunResult, run_rattish_file
from gofer.rattish.trigger_service import TriggerEngine, TriggerRun
from gofer.ui import ticket_patch, worktree_setup
from tests.unit.test_rattish_triggers import START, Clock, _settled

ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@x",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@x",
}

WORKFLOW = """Rattish: 1

Workflow:
  name: Change Files
  workspace: worktree

Node change:
  type: bash-command
  command: |
    pwd
    echo changed > notes.txt
  to: read

Node read:
  type: agent
  provider: claude-code
  prompt: Read notes.txt.
  needs: change
"""

QUIET = """Rattish: 1

Workflow:
  name: Quiet
  workspace: worktree

Node look:
  type: bash-command
  command: pwd && cat README
"""


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "commit.gpgsign", "false")
    (root / "README").write_text("base\n")
    (root / ".gitignore").write_text("node_modules\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    return root


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


_FAKE_CLAUDE = """#!{python}
import json, os, sys
sys.stdin.read()
argv = sys.argv[1:]
settings = json.loads(argv[argv.index("--settings") + 1])
with open("notes.txt") as notes:
    text = notes.read()
with open({record!r}, "a") as handle:
    git_env = {{key: value for key, value in os.environ.items() if key.startswith("GIT_CONFIG")}}
    call = {{"cwd": os.getcwd(), "notes": text, "settings": settings, "git_env": git_env}}
    handle.write(json.dumps(call) + "\\n")
print(json.dumps({{"type": "result", "subtype": "success", "result": text.strip()}}), flush=True)
"""


class Agent:
    """A fake Claude Code CLI that records the folder each agent step ran in."""

    def __init__(self, record: Path) -> None:
        self.record = record

    @property
    def calls(self) -> list[dict[str, Any]]:
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text().splitlines()]


@pytest.fixture(autouse=True)
def agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Agent:

    from gofer.subscriptions import claude_code
    from gofer.ui import claude_sandbox

    record = tmp_path / "claude-calls.jsonl"
    script = tmp_path / "claude"
    script.write_text(_FAKE_CLAUDE.format(python=sys.executable, record=str(record)))
    script.chmod(0o755)
    monkeypatch.setattr(claude_code, "resolve_provider_executable", lambda _id: str(script))
    monkeypatch.setattr(claude_sandbox, "unavailable_reason", lambda platform=None: None)
    return Agent(record)


def _source(folder: Path, text: str = WORKFLOW) -> Path:
    path = folder / "workflow.rattish"
    path.write_text(text, encoding="utf-8")
    return path


def _run(source: Path, data_dir: Path, **options: Any) -> RattishRunResult:
    from gofer.subscriptions.claude_code import ClaudeCodeSubscription

    subscriptions = {"claude_code": ClaudeCodeSubscription()}
    return asyncio.run(
        run_rattish_file(source, data_dir=data_dir, subscriptions=subscriptions, **options)
    )


def _save(result: RattishRunResult) -> Any:
    return lambda record: run_worktrees.update_record(result.path, record)


def _stored(result: RattishRunResult) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(result.path.read_text(encoding="utf-8")))


def _git_config_pairs(env: dict[str, str]) -> list[tuple[str, str]]:
    count = int(env.get("GIT_CONFIG_COUNT", "0"))
    return [(env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"]) for i in range(count)]


def _branches(repo: Path) -> list[str]:
    return git(repo, "branch", "--format=%(refname:short)").splitlines()


def test_workspace_compiles_to_project_or_worktree(tmp_path: Path, data_dir: Path) -> None:
    plain = _source(tmp_path, QUIET.replace("  workspace: worktree\n", ""))
    assert compile_rattish_file(plain, data_dir=data_dir).ir["workflow"]["workspace"] == "project"
    (tmp_path / "other").mkdir()
    chosen = _source(tmp_path / "other", QUIET)
    ir = compile_rattish_file(chosen, data_dir=data_dir).ir
    assert ir["workflow"]["workspace"] == "worktree"
    (tmp_path / "bad").mkdir()
    wrong = _source(tmp_path / "bad", QUIET.replace("workspace: worktree", "workspace: tree"))
    with pytest.raises(RattishError) as caught:
        compile_rattish_file(wrong, data_dir=data_dir)
    assert [item.message for item in caught.value.diagnostics] == [
        "workspace must be project or worktree."
    ]


def test_a_run_works_in_its_own_worktree_and_lands_by_fast_forward(
    repo: Path, data_dir: Path, agent: Agent
) -> None:
    source = _source(repo)
    git(repo, "add", "workflow.rattish")
    git(repo, "commit", "-qm", "workflow")

    result = _run(source, data_dir)

    document = result.document
    assert document["status"] == "passed", document["error"]
    record = document["worktree"]
    checkout = Path(record["checkout"])
    run_id = document["run_id"]
    assert record["state"] == "pending"
    assert record["branch"] == f"raticode/run/{document['workflow']['id']}-{run_id}"
    assert record["base_branch"] == "main"
    assert record["files_changed"] == 1
    assert checkout == data_dir.resolve() / "rattish-worktrees" / "project" / run_id
    # The shell step and the agent step both worked in the worktree, not the project.
    assert document["latest_node_outputs"]["change"]["stdout"].strip() == str(checkout)
    [call] = agent.calls
    assert call["cwd"] == str(checkout)
    assert call["notes"] == "changed\n"
    # The agent's sandbox opens the worktree, which lives in Raticode's data folder.
    assert str(checkout) in call["settings"]["sandbox"]["filesystem"]["allowRead"]
    # Of the repository's .git folder, the step may write only what its own branch needs.
    common = (repo / ".git").resolve()
    assert call["settings"]["sandbox"]["filesystem"]["allowWrite"][1:] == [
        str(common / "objects"),
        str(common / "worktrees" / run_id),
        str(common / "refs" / "heads" / "raticode" / "run"),
        str(common / "logs" / "refs" / "heads" / "raticode" / "run"),
    ]
    # Git never repacks there, and settings already in the environment are kept.
    settings = _git_config_pairs(call["git_env"])
    assert settings[-2:] == [("gc.auto", "0"), ("maintenance.auto", "false")]
    assert settings[:-2] == _git_config_pairs(dict(os.environ))
    assert not (repo / "notes.txt").exists()
    # Raticode committed the change on the run's branch.
    assert git(checkout, "status", "--porcelain") == ""
    assert git(repo, "show", f"{record['branch']}:notes.txt") == "changed"
    assert run_worktrees.unlanded_run_ids(data_dir, "project") == [run_id]

    changes = run_worktrees.changes(document)
    assert [item["path"] for item in changes["files"]] == ["notes.txt"]
    assert changes["files"][0]["modified"] == "changed\n"
    assert changes["merge"]["state"] == "fast_forward"
    assert changes["worktree"]["state"] == "pending"

    landed = run_worktrees.land(document, _save(result), expected_tip=changes["tip"])

    assert landed["how"] == "fast_forward"
    assert (repo / "notes.txt").read_text() == "changed\n"
    assert git(repo, "rev-parse", "HEAD") == record["commit"]
    assert record["branch"] not in _branches(repo)
    assert not checkout.exists()
    assert run_worktrees.unlanded_run_ids(data_dir, "project") == []
    stored = _stored(result)["worktree"]
    assert stored["state"] == "landed"
    assert stored["landed_commit"] == record["commit"]
    with pytest.raises(run_worktrees.RunWorktreeError, match="already landed"):
        run_worktrees.land(_stored(result), _save(result))


def test_land_merges_when_the_project_moved_and_stops_on_conflicts(
    repo: Path, data_dir: Path
) -> None:
    source = _source(repo)
    first = _run(source, data_dir)
    second = _run(source, data_dir)
    # The project moves on after both runs started.
    (repo / "other.txt").write_text("other\n")
    git(repo, "add", "other.txt")
    git(repo, "commit", "-qm", "other")

    merged = run_worktrees.land(first.document, _save(first))
    assert merged["how"] == "clean"
    assert (repo / "notes.txt").read_text() == "changed\n"
    assert len(git(repo, "log", "-1", "--format=%P").split()) == 2

    # Someone then rewrites the file the second run also added: landing it would conflict.
    (repo / "notes.txt").write_text("mine\n")
    git(repo, "commit", "-qam", "mine")
    assert run_worktrees.changes(second.document)["merge"]["state"] == "conflict"
    head = git(repo, "rev-parse", "HEAD")
    with pytest.raises(ticket_patch.LandConflict) as caught:
        run_worktrees.land(second.document, _save(second))
    assert caught.value.files == ["notes.txt"]
    assert git(repo, "rev-parse", "HEAD") == head
    assert _stored(second)["worktree"]["state"] == "pending"


def test_land_is_refused_while_the_project_has_uncommitted_changes(
    repo: Path, data_dir: Path
) -> None:
    result = _run(_source(repo), data_dir)
    (repo / "README").write_text("edited by hand\n")
    head = git(repo, "rev-parse", "HEAD")

    with pytest.raises(ticket_patch.PatchError, match="uncommitted changes"):
        run_worktrees.land(result.document, _save(result))

    assert git(repo, "rev-parse", "HEAD") == head
    assert (repo / "README").read_text() == "edited by hand\n"
    assert Path(result.document["worktree"]["checkout"]).is_dir()


def test_discard_removes_the_worktree_and_branch(repo: Path, data_dir: Path) -> None:
    result = _run(_source(repo), data_dir)
    record = result.document["worktree"]

    discarded = run_worktrees.discard(result.document, _save(result))

    assert discarded["discarded"] is True
    assert not Path(record["checkout"]).exists()
    assert record["branch"] not in _branches(repo)
    assert not (repo / "notes.txt").exists()
    assert _stored(result)["worktree"]["state"] == "discarded"
    assert run_worktrees.changes(_stored(result))["files"] == []
    with pytest.raises(run_worktrees.RunWorktreeError, match="already discarded"):
        run_worktrees.discard(_stored(result), _save(result))


def test_a_run_that_changes_nothing_cleans_up(repo: Path, data_dir: Path) -> None:
    result = _run(_source(repo, QUIET), data_dir)

    record = result.document["worktree"]
    assert result.document["status"] == "passed", result.document["error"]
    assert record["state"] == "no_changes"
    assert not Path(record["checkout"]).exists()
    assert record["branch"] not in _branches(repo)
    assert run_worktrees.unlanded_run_ids(data_dir, "project") == []
    # The uncommitted workflow file isn't in the worktree, which has only commits.
    assert "base" in result.document["latest_node_outputs"]["look"]["stdout"]


def test_a_step_that_points_head_at_main_gets_no_commit_there(repo: Path, data_dir: Path) -> None:
    """A step can rewrite its worktree's HEAD; Raticode's own commit must not follow it."""
    planned = run_worktrees.plan(data_dir, repo, "wf", "run-head")
    run_worktrees.create(planned)
    main = git(repo, "rev-parse", "refs/heads/main")
    git(planned.checkout, "symbolic-ref", "HEAD", "refs/heads/main")
    (planned.root / "planted.txt").write_text("not reviewed\n")

    finished = run_worktrees.finish(planned.document(), "Run of wf")

    assert finished["state"] == "pending"
    assert "not the run's branch" in finished["message"]
    assert git(repo, "rev-parse", "refs/heads/main") == main
    with pytest.raises(ticket_patch.PatchError, match="won't commit there"):
        ticket_patch.commit_leftovers(str(planned.checkout), "Ticket", branch=planned.branch)
    assert git(repo, "rev-parse", "refs/heads/main") == main

    git(planned.checkout, "symbolic-ref", "HEAD", f"refs/heads/{planned.branch}")
    assert run_worktrees.commit_changes(planned.checkout, "Run of wf", branch=planned.branch)
    assert git(repo, "show", f"{planned.branch}:planted.txt") == "not reviewed"
    assert git(repo, "rev-parse", "refs/heads/main") == main


def test_a_project_in_a_subfolder_runs_in_the_same_subfolder(repo: Path, data_dir: Path) -> None:
    project = repo / "apps" / "site"
    project.mkdir(parents=True)
    (project / "README").write_text("site\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "site")

    result = _run(_source(project, QUIET), data_dir)

    record = result.document["worktree"]
    out = result.document["latest_node_outputs"]["look"]["stdout"].splitlines()
    assert out[0] == str(Path(record["checkout"]) / "apps" / "site")
    assert out[1] == "site"
    assert record["root"] == out[0]


def test_absolute_working_dirs_inside_the_repository_move_into_the_worktree(
    repo: Path, data_dir: Path
) -> None:
    text = QUIET.replace(
        "  command: pwd && cat README\n", f"  command: pwd\n  working-dir: {repo}/sub\n"
    )
    (repo / "sub").mkdir()
    (repo / "sub" / "keep").write_text("")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "sub")

    result = _run(_source(repo, text), data_dir)

    checkout = result.document["worktree"]["checkout"]
    assert result.document["latest_node_outputs"]["look"]["stdout"].strip() == f"{checkout}/sub"


def test_approved_project_setup_runs_in_the_worktree_first(repo: Path, data_dir: Path) -> None:
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "lib.js").write_text("lib\n")
    (repo / ".raticode").mkdir()
    (repo / ".raticode" / "worktree-setup.json").write_text(
        json.dumps({"link": ["node_modules"], "setup": "echo ready > node_modules.flag"})
    )
    (repo / ".gitignore").write_text("node_modules\nnode_modules.flag\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "setup")
    digest = worktree_setup.status(data_dir, repo)["file"]["sha256"]
    worktree_setup.approve(data_dir, repo, digest)
    text = QUIET.replace("pwd && cat README", "cat node_modules/lib.js node_modules.flag")

    result = _run(_source(repo, text), data_dir)

    document = result.document
    assert document["status"] == "passed", document["error"]
    assert document["worktree"]["setup"]["status"] == "passed"
    assert document["latest_node_outputs"]["look"]["stdout"] == "lib\nready\n"
    # The link and the flag are ignored, so the run changed nothing.
    assert document["worktree"]["state"] == "no_changes"


def test_failed_setup_fails_the_run_before_any_node(repo: Path, data_dir: Path) -> None:
    (repo / ".raticode").mkdir()
    (repo / ".raticode" / "worktree-setup.json").write_text(json.dumps({"setup": "exit 3"}))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "setup")
    worktree_setup.approve(data_dir, repo, worktree_setup.status(data_dir, repo)["file"]["sha256"])

    result = _run(_source(repo), data_dir)

    document = result.document
    assert document["status"] == "failed"
    assert document["error"]["code"] == "RATTISH_WORKTREE_SETUP_FAILED"
    assert document["runs"] == []
    assert document["worktree"]["state"] == "no_changes"
    assert not Path(document["worktree"]["checkout"]).exists()
    assert run_worktrees.unlanded_run_ids(data_dir, "project") == []


def test_at_most_ten_unlanded_worktrees_per_workflow(repo: Path, data_dir: Path) -> None:
    folder = run_worktrees.worktrees_dir(data_dir, "project")
    for index in range(run_worktrees.MAX_UNLANDED):
        (folder / f"run-{index}").mkdir(parents=True)

    result = _run(_source(repo), data_dir)

    assert result.document["status"] == "failed"
    assert result.document["error"]["code"] == "RATTISH_WORKTREE_LIMIT"
    assert "10 run worktrees waiting" in result.document["error"]["message"]
    assert "worktree" not in result.document
    assert not (repo / "notes.txt").exists()


class Runs:
    def __init__(self) -> None:
        self.started: list[TriggerRun] = []
        self.finished = 0

    def __call__(self, run: TriggerRun) -> RattishRunResult:
        self.started.append(run)
        self.finished += 1
        return cast(RattishRunResult, type("R", (), {"document": {"status": "passed"}})())


def test_triggered_runs_are_skipped_with_a_reason_past_the_limit(
    repo: Path, data_dir: Path
) -> None:
    source = _source(repo, QUIET)
    runs, clock = Runs(), Clock()
    engine = TriggerEngine(
        data_dir, runner=runs, clock=clock, enable_webhooks=False, enable_watchers=False
    )
    record, _ = engine.store.create(
        kind="cron",
        source_path=source,
        config={"expression": "* * * * *", "timezone": "UTC"},
        now=START - timedelta(seconds=20),
    )
    engine.become_owner()
    try:
        clock.advance(60)
        engine.tick()
        _settled(engine, cast(Any, runs), 1)
        folder = run_worktrees.worktrees_dir(data_dir, "project")
        for index in range(run_worktrees.MAX_UNLANDED):
            (folder / f"run-{index}").mkdir(parents=True)
        clock.advance(60)
        engine.tick()
        _settled(engine, cast(Any, runs), 1)
    finally:
        engine.stop()

    assert len(runs.started) == 1
    latest = engine.store.events(trigger_id=record.id)[0]
    assert latest.status == "skipped"
    assert latest.message.startswith("Skipped. This workflow already has 10 run worktrees")


def test_preflight_warns_without_a_setup_file_and_fails_outside_git(
    repo: Path, tmp_path: Path, data_dir: Path
) -> None:
    ir = compile_rattish_file(_source(repo, QUIET), data_dir=data_dir).ir
    codes = [
        (item.code, item.severity) for item in run_preflight(ir, data_dir=data_dir).diagnostics
    ]
    assert codes == [("RATTISH_PREFLIGHT_WORKTREE_SETUP_MISSING", "warning")]

    (repo / ".raticode").mkdir()
    (repo / ".raticode" / "worktree-setup.json").write_text("{}")
    assert run_preflight(ir, data_dir=data_dir).diagnostics == ()

    loose = tmp_path / "loose"
    loose.mkdir()
    ir = compile_rattish_file(_source(loose, QUIET), data_dir=data_dir).ir
    found = run_preflight(ir, data_dir=data_dir).diagnostics
    assert [(item.code, item.severity) for item in found] == [
        ("RATTISH_PREFLIGHT_WORKTREE_UNAVAILABLE", "error")
    ]
    assert "isn't in a git repository" in found[0].message


def test_test_sets_and_project_workflows_keep_running_in_the_project(
    repo: Path, data_dir: Path
) -> None:
    result = _run(_source(repo), data_dir, use_worktree=False)

    assert result.document["status"] == "passed", result.document["error"]
    assert "worktree" not in result.document
    assert (repo / "notes.txt").read_text() == "changed\n"


def test_retention_keeps_runs_whose_changes_wait(repo: Path, data_dir: Path) -> None:
    from gofer.rattish.run_retention import prune_run_records

    waiting = _run(_source(repo), data_dir)
    quiet = _run(_source(repo, QUIET), data_dir)

    pruned = prune_run_records(
        data_dir, "project", waiting.path.parent, keep_last=0, keep_days=0, dry_run=False
    )

    assert pruned.deleted == [quiet.document["run_id"]]
    assert waiting.path.exists()


def test_the_studio_toggle_adds_and_removes_workspace_worktree(tmp_path: Path) -> None:
    from gofer.ui.api import (
        create_registered_workflow_payload,
        mutate_rattish_document_payload,
        open_rattish_document_payload,
    )

    project = tmp_path / "project"
    project.mkdir()
    registered = create_registered_workflow_payload("Toggle", project, registry_dir=tmp_path)
    source = Path(registered["sourcePath"])
    # No blank line before the first node: the new field still gets its own line.
    source.write_text(
        "Rattish: 1\nWorkflow:\n  name: Toggle\nNode a:\n  type: bash-command\n  command: pwd\n",
        encoding="utf-8",
    )
    opened = open_rattish_document_payload("toggle", tmp_path)
    on = mutate_rattish_document_payload(
        "toggle",
        [
            {
                "kind": "set_field",
                "target": "workflow",
                "field": "workspace",
                "valueSource": "worktree",
            }
        ],
        opened["sourceRevision"],
        tmp_path,
    )
    assert "  name: Toggle\n  workspace: worktree\nNode a:\n" in on["source"]
    assert on["workflow"]["fields"]["workspace"]["value"] == "worktree"
    off = mutate_rattish_document_payload(
        "toggle",
        [{"kind": "remove_field", "target": "workflow", "field": "workspace"}],
        on["sourceRevision"],
        tmp_path,
    )
    assert "workspace" not in off["source"]


SCRIPTED = """Rattish: 1

Workflow:
  name: Scripted
  workspace: worktree

Node step:
  type: python-script
  script-path: scripts/step.py
"""
STEP_PY = "from pathlib import Path\nPath('ran.txt').write_text({text!r})\n"


def test_a_triggered_run_checks_the_worktree_copies_of_approved_files(
    repo: Path, data_dir: Path
) -> None:
    from tests.unit.test_trigger_run_file_checks import _fire, _node_error

    source = _source(repo, SCRIPTED)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "step.py").write_text(STEP_PY.format(text="committed"))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "step")

    _, _, passed = _fire(data_dir, source, label="Nightly")
    assert passed["status"] == "passed", passed["error"]
    assert git(repo, "show", f"{passed['worktree']['branch']}:ran.txt") == "committed"

    # Approved now: an uncommitted edit. The worktree has the committed text, which isn't
    # what the user approved, so the step refuses to run it.
    (repo / "scripts" / "step.py").write_text(STEP_PY.format(text="edited"))
    _, _, refused = _fire(data_dir, source, label="Nightly")
    assert refused["status"] == "failed"
    error = _node_error(refused, "step")
    assert error["code"] == "RATTISH_APPROVED_FILE_CHANGED"
    assert refused["worktree"]["state"] == "no_changes"


# --- What a sandboxed agent step may change in the repository's .git folder ----------


def _sandboxed_step(
    repo: Path, data_dir: Path, *, step_env: dict[str, str] | None = None
) -> tuple[run_worktrees.RunWorktree, dict[str, Any], dict[str, str]]:
    """A run's worktree, and the Claude Code settings and env of an agent step in it."""
    from gofer.core.provider_profiles import ResolvedProviderSettings
    from gofer.rattish import agent_sandbox

    planned = run_worktrees.plan(data_dir, repo, "wf", "run-1")
    run_worktrees.create(planned)
    with agent_sandbox.run_worktree(planned.document()):
        prepared = agent_sandbox.prepare_step(
            "claude_code",
            {"env": step_env or {}},
            ResolvedProviderSettings(subscription="claude_code"),
            working_dir=planned.root,
            extra_paths=[],
            data_dir=data_dir,
            run_id="run-1",
            triggered=False,
            platform="linux",
        )
    args = prepared.settings.extra_args
    return planned, json.loads(args[args.index("--settings") + 1]), prepared.env


def _git_writes(settings: dict[str, Any], common: Path) -> list[str]:
    return [
        path
        for path in settings["sandbox"]["filesystem"]["allowWrite"]
        if Path(path).is_relative_to(common)
    ]


def test_a_run_step_may_write_only_its_own_parts_of_the_git_folder(
    repo: Path, data_dir: Path
) -> None:
    common = (repo / ".git").resolve()
    planned, settings, env = _sandboxed_step(
        repo, data_dir, step_env={"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "a.b"}
    )

    assert _git_writes(settings, common) == [
        str(common / "objects"),
        str(common / "worktrees" / "run-1"),
        str(common / "refs/heads/raticode/run"),
        str(common / "logs/refs/heads/raticode/run"),
    ]
    assert str(common) not in settings["sandbox"]["filesystem"]["allowWrite"]
    # The admin folder's pointers and worktree config stay read-only inside it.
    admin = common / "worktrees" / "run-1"
    deny_write = settings["sandbox"]["filesystem"]["denyWrite"]
    for name in ("commondir", "gitdir", "config.worktree"):
        assert str(admin / name) in deny_write
    # The step's own git settings come first.
    assert env["GIT_CONFIG_COUNT"] == "3"
    assert (env["GIT_CONFIG_KEY_1"], env["GIT_CONFIG_VALUE_1"]) == ("gc.auto", "0")
    assert "GIT_CONFIG_KEY_0" not in env


def test_a_rewritten_dot_git_file_does_not_open_another_git_folder(
    repo: Path, data_dir: Path, tmp_path: Path
) -> None:
    from gofer.rattish import agent_sandbox

    common = (repo / ".git").resolve()
    planned = run_worktrees.plan(data_dir, repo, "wf", "run-1")
    run_worktrees.create(planned)
    # An earlier step pointed the worktree's .git file at a folder of its choosing.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (planned.checkout / ".git").write_text(f"gitdir: {elsewhere}\n")

    with agent_sandbox.run_worktree(planned.document()):
        access = agent_sandbox.run_git_access(planned.root)
        # A step working outside the run's worktree keeps the general rule.
        assert agent_sandbox.run_git_access(tmp_path) is None
    assert access is not None
    # Both come from the repository's side, which still names the real worktree folder.
    assert access.common_dir == common
    assert access.admin_dir == common / "worktrees" / "run-1"
    assert all(path.is_relative_to(common) for path in access.writable())
    assert agent_sandbox.run_git_access(planned.root) is None  # No run is executing.


def test_a_run_step_writes_no_git_folder_it_cannot_find(tmp_path: Path) -> None:
    from gofer.rattish import agent_sandbox

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    record = {"repository": str(tmp_path / "not-a-repo"), "checkout": str(checkout)}
    (tmp_path / "not-a-repo").mkdir()
    with agent_sandbox.run_worktree(record):
        access = agent_sandbox.run_git_access(checkout)
    assert access is not None
    assert access.writable() == []


def _check_step_git(run: Any, repo: Path, planned: run_worktrees.RunWorktree) -> None:
    """A step commits on its run branch; every other branch and tag stays put."""
    main = git(repo, "rev-parse", "refs/heads/main")
    (planned.root / "step.txt").write_text("from the step\n")
    added = run("add", "step.txt")
    assert added.returncode == 0, added.stderr
    committed = run("commit", "-q", "-m", "step commit")
    assert committed.returncode == 0, committed.stderr
    assert git(repo, "show", f"{planned.branch}:step.txt") == "from the step"

    moved = run("update-ref", "refs/heads/main", "HEAD")
    assert moved.returncode != 0
    tagged = run("tag", "v1")
    assert tagged.returncode != 0
    branched = run("branch", "other")
    assert branched.returncode != 0
    assert git(repo, "rev-parse", "refs/heads/main") == main
    assert git(repo, "tag", "--list") == ""
    assert "other" not in _branches(repo)


@pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="needs POSIX file permissions that apply to this user",
)
def test_the_step_git_folders_are_enough_to_commit_and_nothing_more(
    repo: Path, data_dir: Path
) -> None:
    """The same check as the bubblewrap test below, with file permissions standing in."""
    common = (repo / ".git").resolve()
    planned, settings, env = _sandboxed_step(repo, data_dir)
    writable = [Path(path) for path in _git_writes(settings, common)]
    folders = [Path(root) for root, _, _ in os.walk(common)]
    try:
        for folder in folders:
            if not any(folder.is_relative_to(path) for path in writable):
                folder.chmod(0o555)

        def run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", "-C", str(planned.root), *args],
                capture_output=True,
                text=True,
                env={**os.environ, **env},
            )

        _check_step_git(run, repo, planned)
    finally:
        for folder in folders:
            folder.chmod(0o755)


def _bubblewrap_starts() -> bool:
    if not sys.platform.startswith("linux") or shutil.which("bwrap") is None:
        return False
    probe = subprocess.run(
        ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "true"],
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


@pytest.mark.skipif(not _bubblewrap_starts(), reason="needs bubblewrap that can start here")
def test_a_sandboxed_step_commits_on_its_run_branch_but_cannot_move_main(
    repo: Path, data_dir: Path
) -> None:
    """Runs git in bubblewrap the way Claude Code's Linux sandbox applies the settings:
    everything read-only, then the working folder and each allowWrite path writable, then
    each denyWrite path that exists read-only again."""
    planned, settings, env = _sandboxed_step(repo, data_dir)
    command = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
    filesystem = settings["sandbox"]["filesystem"]
    for path in [str(planned.root), *filesystem["allowWrite"]]:
        command += ["--bind", path, path]
    for path in filesystem["denyWrite"]:
        if Path(path).exists():
            command += ["--ro-bind", path, path]

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*command, "--chdir", str(planned.root), "git", *args],
            capture_output=True,
            text=True,
            env={**os.environ, **env},
        )

    _check_step_git(run, repo, planned)
    # The step can't repoint its worktree at another repository or checkout.
    admin = (repo / ".git" / "worktrees" / "run-1").resolve()
    for name in ("commondir", "gitdir"):
        before = (admin / name).read_text()
        rewritten = subprocess.run(
            [*command, "sh", "-c", f"echo /elsewhere > '{admin / name}'"],
            capture_output=True,
            text=True,
        )
        assert rewritten.returncode != 0
        assert (admin / name).read_text() == before


# Deleting a workflow and the startup sweep ------------------------------------------


def _registered(repo: Path, data_dir: Path, name: str = "Change Files") -> Any:
    from gofer.rattish.workspaces import create_registered_workflow

    workflow = create_registered_workflow(repo, name, registry_dir=data_dir)
    workflow.entrypoint.write_text(WORKFLOW, encoding="utf-8")
    return workflow


def _worktree_count(repo: Path) -> int:
    return git(repo, "worktree", "list", "--porcelain").count("worktree ")


def _age(folder: Path, seconds: float) -> None:
    when = time.time() - seconds
    for path in [*folder.iterdir(), folder]:
        os.utime(path, (when, when), follow_symlinks=False)


def test_deleting_a_workflow_removes_its_waiting_worktrees_and_branches(
    repo: Path, data_dir: Path
) -> None:
    from gofer.ui.api import delete_workflow_payload, workflow_run_worktrees_payload

    workflow = _registered(repo, data_dir)
    runs = [_run(workflow.entrypoint, data_dir) for _ in range(2)]
    assert [run.document["worktree"]["state"] for run in runs] == ["pending", "pending"]
    # A worktree whose run record is gone is read from git instead.
    orphan = run_worktrees.plan(data_dir, repo, workflow.workflow_id, "orphan-run")
    run_worktrees.create(orphan)
    assert _worktree_count(repo) == 4
    assert len([b for b in _branches(repo) if b.startswith("raticode/run/")]) == 3

    counted = workflow_run_worktrees_payload(workflow.workflow_id, data_dir)
    assert counted["pending"] == 3
    assert "orphan-run" in counted["runIds"]

    result = delete_workflow_payload(workflow.workflow_id, data_dir)

    assert result["deleted"] is True
    assert result["runWorktreesRemoved"] == 3
    assert result["runWorktreeErrors"] == []
    assert not run_worktrees.worktrees_dir(data_dir, workflow.workflow_id).exists()
    assert _branches(repo) == ["main"]
    assert _worktree_count(repo) == 1
    # The project itself is untouched: nothing was landed.
    assert not (repo / "notes.txt").exists()
    assert git(repo, "log", "--format=%s") == "base"


def test_a_workflow_recreated_with_the_same_name_starts_without_old_worktrees(
    repo: Path, data_dir: Path
) -> None:
    from gofer.ui.api import delete_workflow_payload, workflow_run_worktrees_payload

    workflow = _registered(repo, data_dir)
    old = _run(workflow.entrypoint, data_dir)
    old_branch = old.document["worktree"]["branch"]
    delete_workflow_payload(workflow.workflow_id, data_dir)

    again = _registered(repo, data_dir)

    assert again.workflow_id == workflow.workflow_id
    assert workflow_run_worktrees_payload(again.workflow_id, data_dir)["pending"] == 0
    new = _run(again.entrypoint, data_dir)
    assert new.document["worktree"]["state"] == "pending"
    assert run_worktrees.unlanded_run_ids(data_dir, again.workflow_id) == [
        Path(new.document["worktree"]["checkout"]).name
    ]
    assert old_branch not in _branches(repo)
    assert new.document["worktree"]["branch"] in _branches(repo)


def test_deleting_is_refused_while_a_worktree_run_is_running(
    repo: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.rattish.workspaces import list_registered_workflows
    from gofer.ui import api

    workflow = _registered(repo, data_dir)
    running = ({"run_id": "busy-run", "status": "running"}, {"state": "active"})
    monkeypatch.setattr(api, "_rattish_worktree_records", lambda base, workflow_id: [running])

    with pytest.raises(api.WorkflowUpdateError, match="busy-run of this workflow is still running"):
        api.delete_workflow_payload(workflow.workflow_id, data_dir)

    assert [item.workflow_id for item in list_registered_workflows(registry_dir=data_dir)] == [
        workflow.workflow_id
    ]


def test_the_startup_sweep_removes_old_folders_of_unregistered_workflows(
    repo: Path, data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    kept = _registered(repo, data_dir)
    kept_run = _run(kept.entrypoint, data_dir)
    made: dict[str, run_worktrees.RunWorktree] = {}
    for workflow_id in ("gone-workflow", "fresh-workflow", "file-only"):
        made[workflow_id] = run_worktrees.plan(data_dir, repo, workflow_id, "run-1")
        run_worktrees.create(made[workflow_id])
    # A workflow file run without registering keeps its records here and can still land.
    records = data_dir / "radish" / "runs" / "file-only"
    records.mkdir(parents=True)
    (records / "run-1.json").write_text("{}", encoding="utf-8")
    empty = data_dir / run_worktrees.FOLDER / "empty-folder"
    empty.mkdir()
    day = run_worktrees.STALE_FOLDER_SECONDS
    for workflow_id in ("gone-workflow", "file-only"):
        _age(run_worktrees.worktrees_dir(data_dir, workflow_id), day + 60)
    _age(run_worktrees.worktrees_dir(data_dir, kept.workflow_id), day + 60)
    _age(empty, day + 60)
    _age(run_worktrees.worktrees_dir(data_dir, "fresh-workflow"), day - 600)

    with caplog.at_level(logging.INFO, logger="gofer.rattish.run_worktrees"):
        removed = run_worktrees.sweep_at_start(data_dir)

    gone = run_worktrees.worktrees_dir(data_dir, "gone-workflow")
    assert sorted(removed) == sorted([gone, empty])
    assert not gone.exists() and not empty.exists()
    branches = _branches(repo)
    assert made["gone-workflow"].branch not in branches
    for workflow_id in ("fresh-workflow", "file-only"):
        assert made[workflow_id].branch in branches
        assert made[workflow_id].checkout.is_dir()
    assert kept_run.document["worktree"]["branch"] in branches
    assert Path(kept_run.document["worktree"]["checkout"]).is_dir()
    assert "unregistered workflow gone-workflow: run-1" in caplog.text
    assert "empty-folder: an empty folder" in caplog.text
    assert _worktree_count(repo) == 4


def test_the_startup_sweep_deletes_nothing_when_the_registry_is_unreadable(
    repo: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.rattish import workspaces

    planned = run_worktrees.plan(data_dir, repo, "gone-workflow", "run-1")
    run_worktrees.create(planned)
    _age(planned.checkout.parent, run_worktrees.STALE_FOLDER_SECONDS * 2)

    def unreadable(**_options: Any) -> Any:
        raise workspaces.RattishWorkspaceError("broken registry")

    monkeypatch.setattr(workspaces, "list_registered_workflows", unreadable)

    assert run_worktrees.sweep_at_start(data_dir) == []
    assert planned.checkout.is_dir()
    assert planned.branch in _branches(repo)
