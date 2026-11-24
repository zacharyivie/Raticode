from __future__ import annotations

import json
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, cast

import pytest
from rich.console import Console
from typer.testing import CliRunner

from gofer.cli.commands import schedule as schedule_cmd
from gofer.cli.commands import watch as watch_cmd
from gofer.cli.main import app
from gofer.core.agent import AgentConfig, AgentResult
from gofer.core.graph import GraphNode
from gofer.core.operations import BashCommandOperation, OperationType
from gofer.core.workflow import AgenticWorkflow, WorkflowConfig
from gofer.ui.chat import workflow_chat_prompt_path

runner = CliRunner()


def _unfolded(output: str) -> str:
    """Join the lines Rich folds at the console width, so long temporary paths still match.

    Parallel pytest workers add a ``popen-gwN`` folder to every temporary path.
    """
    return "".join(output.splitlines())


_SIMPLE_TOML = """
[workflow]
id = "simple"
name = "Simple"

[[nodes]]
id = "hello"
type = "bash_command"
command = "echo hello"
"""


def _create_workflow(data_dir: Path, workflow_id: str = "simple") -> Path:
    path = data_dir / f"{workflow_id}.toml"
    path.write_text(
        _SIMPLE_TOML.replace('id = "simple"', f'id = "{workflow_id}"'),
        encoding="utf-8",
    )
    return path


def _create_agent(data_dir: Path, name: str = "Branch Agent") -> str:
    result = runner.invoke(
        app,
        [
            "agent",
            "create",
            "--name",
            name,
            "--subscription",
            "codex",
            "--working-dir",
            str(data_dir),
            "--prompt",
            "hello",
            "--data-dir",
            str(data_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    return name.lower().replace(" ", "-")


class _CliSubscription:
    def __init__(self, success: bool = True, output: str = "agent output") -> None:
        self.calls: list[dict[str, object]] = []
        self.success = success
        self.output = output

    async def execute(
        self,
        prompt: str,
        working_dir: Path,
        tools: list[str],
        mcp_servers: list[str],
        env: dict[str, str],
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
        extra_paths: list[Path] | None = None,
        max_output_bytes: int | None = None,
    ) -> AgentResult:
        self.calls.append(
            {
                "prompt": prompt,
                "working_dir": working_dir,
                "tools": tools,
                "mcp_servers": mcp_servers,
                "env": env,
                "timeout": timeout,
                "cancel_event": cancel_event,
                "extra_paths": extra_paths or [],
                "max_output_bytes": max_output_bytes,
            }
        )
        return AgentResult(
            agent_id="",
            success=self.success,
            output=self.output,
            exit_code=0 if self.success else 7,
            duration_seconds=0.0,
        )


def test_agent_create_prompts_for_required_fields(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["agent", "create", "--data-dir", str(tmp_path)],
        input=f"Prompted Agent\ncodex\n{tmp_path}\nPrompt from stdin\n",
    )

    assert result.exit_code == 0, result.output
    assert "Created agent" in result.output
    wf = AgenticWorkflow.from_file(tmp_path / "prompted-agent.toml")
    cfg = wf.agents["prompted-agent"]
    assert cfg.subscription == "codex"
    assert cfg.working_dir == tmp_path.resolve()
    assert cfg.prompt_path == tmp_path / "prompts" / "prompted-agent.md"
    assert cfg.prompt_path.read_text(encoding="utf-8") == "Prompt from stdin"


def test_agent_create_and_edit_refuse_credentials_in_env(tmp_path: Path) -> None:
    base = ["--data-dir", str(tmp_path), "--name", "Keyed", "--subscription", "codex"]
    base += ["--working-dir", str(tmp_path), "--prompt", "Hi"]
    refused = runner.invoke(
        app, ["agent", "create", *base, "--env", "ANTHROPIC_API_KEY=sk-ant-SECRET"]
    )
    assert refused.exit_code == 2, refused.output
    assert "credential" in refused.output
    assert not (tmp_path / "keyed.toml").exists()

    created = runner.invoke(app, ["agent", "create", *base, "--env", "LOG_LEVEL=debug"])
    assert created.exit_code == 0, created.output
    edited = runner.invoke(
        app, ["agent", "edit", "keyed", "--data-dir", str(tmp_path), "--env", "GH_TOKEN=x"]
    )
    assert edited.exit_code == 2, edited.output
    assert "GH_TOKEN" not in (tmp_path / "keyed.toml").read_text(encoding="utf-8")


def test_agent_create_persists_all_optional_flags(tmp_path: Path) -> None:
    extra_a = tmp_path / "shared-a"
    extra_b = tmp_path / "shared-b"
    extra_a.mkdir()
    extra_b.mkdir()

    result = runner.invoke(
        app,
        [
            "agent",
            "create",
            "--name",
            "Flag Agent",
            "--subscription",
            "codex",
            "--profile",
            "work",
            "--model",
            "gpt-5-mini",
            "--working-dir",
            str(tmp_path),
            "--prompt",
            "flag prompt",
            "--tools",
            "read,write",
            "--mcp-servers",
            "fs,github",
            "--extra-path",
            str(extra_a),
            "--extra-path",
            str(extra_b),
            "--env",
            "REGION=eu",
            "--env",
            "MODE=test",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    wf = AgenticWorkflow.from_file(tmp_path / "flag-agent.toml")
    cfg = wf.agents["flag-agent"]
    assert cfg.profile == "work"
    assert cfg.model == "gpt-5-mini"
    assert cfg.tools == ["read", "write"]
    assert cfg.mcp_servers == ["fs", "github"]
    assert cfg.extra_paths == [extra_a.resolve(), extra_b.resolve()]
    assert cfg.env == {"REGION": "eu", "MODE": "test"}


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--subscription", "missing"], "Invalid subscription"),
        (["--subscription", "codex", "--env", "BROKEN"], "expected KEY=VALUE"),
    ],
)
def test_agent_create_failure_does_not_write_agent_file(
    tmp_path: Path, args: list[str], message: str
) -> None:
    result = runner.invoke(
        app,
        [
            "agent",
            "create",
            "--name",
            "Bad Agent",
            *args,
            "--working-dir",
            str(tmp_path),
            "--prompt",
            "bad prompt",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert message in result.output
    assert not (tmp_path / "bad-agent.toml").exists()


def test_agent_list_filters_by_workflow_and_reports_missing(tmp_path: Path) -> None:
    agent_id = _create_agent(tmp_path)

    filtered = runner.invoke(
        app, ["agent", "list", "--workflow", agent_id, "--data-dir", str(tmp_path)]
    )
    missing = runner.invoke(
        app, ["agent", "list", "--workflow", "missing", "--data-dir", str(tmp_path)]
    )

    assert filtered.exit_code == 0, filtered.output
    assert agent_id in filtered.output
    assert missing.exit_code == 1
    assert "not found" in missing.output


def test_agent_list_empty_and_table_fields(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from gofer.cli.commands import agent as agent_cmd

    # Wide enough that the prompt path column never folds, however long tmp_path is.
    monkeypatch.setattr(agent_cmd, "console", Console(width=1000))
    empty = runner.invoke(app, ["agent", "list", "--data-dir", str(tmp_path)])

    assert empty.exit_code == 0, empty.output
    assert "No agents found" in empty.output

    agent_id = _create_agent(tmp_path, "Table Agent")
    result = runner.invoke(
        app,
        [
            "agent",
            "edit",
            agent_id,
            "--profile",
            "daily",
            "--model",
            "g5",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output

    listed = runner.invoke(app, ["agent", "list", "--data-dir", str(tmp_path)])

    assert listed.exit_code == 0, listed.output
    assert "table-agent" in listed.output
    assert "daily" in listed.output
    assert "g5" in listed.output
    assert str(tmp_path / "prompts" / "table-agent.md") in listed.output


def test_agent_edit_rejects_invalid_env_and_subscription(tmp_path: Path) -> None:
    agent_id = _create_agent(tmp_path)

    invalid_env = runner.invoke(
        app,
        [
            "agent",
            "edit",
            agent_id,
            "--env",
            "BROKEN",
            "--data-dir",
            str(tmp_path),
        ],
    )
    invalid_sub = runner.invoke(
        app,
        [
            "agent",
            "edit",
            agent_id,
            "--subscription",
            "bad",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert invalid_env.exit_code == 1
    assert "expected KEY=VALUE" in invalid_env.output
    assert invalid_sub.exit_code == 1
    assert "Invalid subscription" in invalid_sub.output


def test_agent_edit_updates_persisted_config(tmp_path: Path) -> None:
    agent_id = _create_agent(tmp_path)
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    result = runner.invoke(
        app,
        [
            "agent",
            "edit",
            agent_id,
            "--subscription",
            "claude_code",
            "--working-dir",
            str(work_dir),
            "--prompt",
            "updated prompt",
            "--tools",
            "read,write",
            "--mcp-servers",
            "server-a,server-b",
            "--env",
            "A=1",
            "--env",
            "B=2",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    wf = AgenticWorkflow.from_file(tmp_path / f"{agent_id}.toml")
    cfg = wf.agents[agent_id]
    assert cfg.subscription == "claude_code"
    assert cfg.working_dir == work_dir.resolve()
    prompt_path = cfg.prompt_path
    assert prompt_path is not None
    assert prompt_path.read_text(encoding="utf-8") == "updated prompt"
    assert cfg.tools == ["read", "write"]
    assert cfg.mcp_servers == ["server-a", "server-b"]
    assert cfg.env == {"A": "1", "B": "2"}


def test_agent_edit_clears_profile_model_and_replaces_extra_paths(tmp_path: Path) -> None:
    agent_id = _create_agent(tmp_path)
    first_extra = tmp_path / "first-extra"
    next_extra = tmp_path / "next-extra"
    first_extra.mkdir()
    next_extra.mkdir()

    initial = runner.invoke(
        app,
        [
            "agent",
            "edit",
            agent_id,
            "--profile",
            "fast",
            "--model",
            "gpt-5-mini",
            "--extra-path",
            str(first_extra),
            "--data-dir",
            str(tmp_path),
        ],
    )
    cleared = runner.invoke(
        app,
        [
            "agent",
            "edit",
            agent_id,
            "--profile",
            "",
            "--model",
            "",
            "--extra-path",
            str(next_extra),
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert initial.exit_code == 0, initial.output
    assert cleared.exit_code == 0, cleared.output
    cfg = AgenticWorkflow.from_file(tmp_path / f"{agent_id}.toml").agents[agent_id]
    assert cfg.profile is None
    assert cfg.model is None
    assert cfg.extra_paths == [next_extra.resolve()]


def test_agent_edit_interactive_cancel_and_save(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent_id = _create_agent(tmp_path)
    import gofer.cli.tui_editor as tui_editor

    class CancelEditor:
        def __init__(self, sections: list[tui_editor.Section], title: str) -> None:
            self.sections = sections

        def run(self) -> bool:
            self.sections[0].fields[1].value = "claude_code"
            return False

    monkeypatch.setattr(tui_editor, "FieldEditorApp", CancelEditor)
    cancelled = runner.invoke(app, ["agent", "edit", agent_id, "--data-dir", str(tmp_path)])
    assert cancelled.exit_code == 0, cancelled.output
    assert "Edit cancelled" in cancelled.output
    assert (
        AgenticWorkflow.from_file(tmp_path / f"{agent_id}.toml").agents[agent_id].subscription
        == "codex"
    )

    class SaveEditor:
        def __init__(self, sections: list[tui_editor.Section], title: str) -> None:
            self.sections = sections

        def run(self) -> bool:
            self.sections[0].fields[1].value = "claude_code"
            self.sections[0].fields[4].value = ["inspect"]
            return True

    monkeypatch.setattr(tui_editor, "FieldEditorApp", SaveEditor)
    saved = runner.invoke(app, ["agent", "edit", agent_id, "--data-dir", str(tmp_path)])

    assert saved.exit_code == 0, saved.output
    assert "Updated agent" in saved.output
    cfg = AgenticWorkflow.from_file(tmp_path / f"{agent_id}.toml").agents[agent_id]
    assert cfg.subscription == "claude_code"
    assert cfg.tools == ["inspect"]


def test_agent_rm_confirmation_and_managed_prompt_cleanup(tmp_path: Path) -> None:
    cancel_id = _create_agent(tmp_path, "Cancel Agent")
    cancel = runner.invoke(
        app,
        ["agent", "rm", cancel_id, "--data-dir", str(tmp_path)],
        input="n\n",
    )
    assert cancel.exit_code != 0
    assert (tmp_path / f"{cancel_id}.toml").exists()

    remove_id = _create_agent(tmp_path, "Remove Agent")
    prompt_path = tmp_path / "prompts" / f"{remove_id}.md"
    assert prompt_path.exists()
    removed = runner.invoke(app, ["agent", "rm", remove_id, "--yes", "--data-dir", str(tmp_path)])

    assert removed.exit_code == 0, removed.output
    assert not (tmp_path / f"{remove_id}.toml").exists()
    assert not prompt_path.exists()


def test_agent_rm_updates_workflow_without_deleting_unmanaged_prompt(
    tmp_path: Path,
) -> None:
    unmanaged_prompt = tmp_path / "unmanaged.md"
    unmanaged_prompt.write_text("keep me", encoding="utf-8")
    managed_prompt = tmp_path / "prompts" / "primary.md"
    managed_prompt.parent.mkdir()
    managed_prompt.write_text("delete me", encoding="utf-8")

    wf = AgenticWorkflow(WorkflowConfig(id="mixed-flow", name="Mixed Flow"))
    wf.register_agent(
        AgentConfig(
            agent_id="primary",
            subscription="codex",
            working_dir=tmp_path,
            prompt_path=managed_prompt,
        )
    )
    wf.register_agent(
        AgentConfig(
            agent_id="secondary",
            subscription="codex",
            working_dir=tmp_path,
            prompt_path=unmanaged_prompt,
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="hello",
            operation=BashCommandOperation(
                type=OperationType.BASH_COMMAND,
                command="echo hello",
            ),
        )
    )
    wf.to_file(tmp_path / "mixed-flow.toml")

    removed = runner.invoke(app, ["agent", "rm", "primary", "--yes", "--data-dir", str(tmp_path)])
    missing = runner.invoke(app, ["agent", "rm", "missing", "--yes", "--data-dir", str(tmp_path)])

    assert removed.exit_code == 0, removed.output
    assert (tmp_path / "mixed-flow.toml").exists()
    reloaded = AgenticWorkflow.from_file(tmp_path / "mixed-flow.toml")
    assert "primary" not in reloaded.agents
    assert "secondary" in reloaded.agents
    assert "hello" in reloaded.graph._nodes
    assert not managed_prompt.exists()
    assert unmanaged_prompt.exists()
    assert missing.exit_code == 1
    assert "not found" in missing.output


def test_agent_run_success_failure_and_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent_id = _create_agent(tmp_path)
    success_sub = _CliSubscription(success=True, output="done")

    from gofer.cli.commands import agent as agent_cmd

    monkeypatch.setattr(agent_cmd, "_SUBSCRIPTIONS", {"codex": success_sub})
    success = runner.invoke(app, ["agent", "run", agent_id, "--data-dir", str(tmp_path)])

    failure_sub = _CliSubscription(success=False, output="bad result")
    monkeypatch.setattr(agent_cmd, "_SUBSCRIPTIONS", {"codex": failure_sub})
    failure = runner.invoke(app, ["agent", "run", agent_id, "--data-dir", str(tmp_path)])
    missing = runner.invoke(app, ["agent", "run", "missing", "--data-dir", str(tmp_path)])

    assert success.exit_code == 0, success.output
    assert "done" in success.output
    assert success_sub.calls
    assert failure.exit_code == 1
    assert "Agent failed (exit 7)" in failure.output
    assert "bad result" in failure.output
    assert missing.exit_code == 1
    assert "not found" in missing.output


def test_agent_run_unknown_subscription_and_invalid_extra_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent_id = _create_agent(tmp_path)

    from gofer.cli.commands import agent as agent_cmd

    monkeypatch.setattr(agent_cmd, "_SUBSCRIPTIONS", {})
    unknown = runner.invoke(app, ["agent", "run", agent_id, "--data-dir", str(tmp_path)])
    assert unknown.exit_code == 1
    assert "Unknown subscription 'codex'" in unknown.output

    wf = AgenticWorkflow.from_file(tmp_path / f"{agent_id}.toml")
    wf.agents[agent_id] = wf.agents[agent_id].model_copy(
        update={"extra_paths": [tmp_path / "missing-extra"]}
    )
    wf.to_file(tmp_path / f"{agent_id}.toml")
    monkeypatch.setattr(agent_cmd, "_SUBSCRIPTIONS", {"codex": _CliSubscription()})
    invalid_extra = runner.invoke(app, ["agent", "run", agent_id, "--data-dir", str(tmp_path)])

    assert invalid_extra.exit_code == 1
    assert "extra_paths entry does not exist" in invalid_extra.output


def test_agent_run_displays_external_extra_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    agent_id = _create_agent(tmp_path)
    extra_dir = tmp_path_factory.mktemp("agent-shared-access")
    wf = AgenticWorkflow.from_file(tmp_path / f"{agent_id}.toml")
    wf.agents[agent_id] = wf.agents[agent_id].model_copy(update={"extra_paths": [extra_dir]})
    wf.to_file(tmp_path / f"{agent_id}.toml")
    sub = _CliSubscription(success=True, output="done")

    from gofer.cli.commands import agent as agent_cmd

    monkeypatch.setattr(agent_cmd, "_SUBSCRIPTIONS", {"codex": sub})
    result = runner.invoke(app, ["agent", "run", agent_id, "--data-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "Agent filesystem access outside working_dir" in result.output
    assert "outside working_dir" in result.output
    assert str(extra_dir.resolve()) in _unfolded(result.output)
    assert sub.calls[0]["extra_paths"] == [extra_dir.resolve()]


class _FakeScheduler:
    jobs: list[dict[str, str]] = []
    removed: list[str] = []
    started_paths: list[Path] = []
    shutdowns = 0

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path

    def add_workflow(self, workflow: AgenticWorkflow, workflow_path: Path) -> None:
        if workflow.config.schedule is None:
            raise ValueError(f"Workflow '{workflow.config.id}' has no schedule configured")
        self.jobs.append(
            {
                "id": workflow.config.id,
                "name": workflow.config.name,
                "next_run": "soon",
            }
        )

    def remove_workflow(self, workflow_id: str) -> None:
        self.removed.append(workflow_id)

    def list_workflows(self) -> list[dict[str, str]]:
        return list(self.jobs)

    def start(self) -> None:
        self.started_paths.append(self.db_path or Path(""))

    def shutdown(self) -> None:
        type(self).shutdowns += 1


@pytest.fixture(autouse=False)
def fake_scheduler(monkeypatch: pytest.MonkeyPatch) -> type[_FakeScheduler]:
    _FakeScheduler.jobs = []
    _FakeScheduler.removed = []
    _FakeScheduler.started_paths = []
    _FakeScheduler.shutdowns = 0
    monkeypatch.setattr(schedule_cmd, "_get_scheduler", lambda db: _FakeScheduler(db))
    return _FakeScheduler


def test_schedule_remove_and_empty_list(
    fake_scheduler: type[_FakeScheduler], tmp_path: Path
) -> None:
    list_result = runner.invoke(app, ["schedule", "list", "--db", str(tmp_path / "s.db")])
    remove_result = runner.invoke(
        app, ["schedule", "remove", "old-flow", "--db", str(tmp_path / "s.db")]
    )

    assert list_result.exit_code == 0, list_result.output
    assert "No scheduled workflows" in list_result.output
    assert remove_result.exit_code == 0, remove_result.output
    assert fake_scheduler.removed == ["old-flow"]


def test_schedule_start_foreground_uses_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(schedule_cmd, "_run_foreground", lambda db: calls.append(db))

    result = runner.invoke(
        app, ["schedule", "start", "--foreground", "--db", str(tmp_path / "sched.db")]
    )

    assert result.exit_code == 0, result.output
    assert calls == [tmp_path / "sched.db"]


def test_schedule_foreground_shutdowns_scheduler(
    fake_scheduler: type[_FakeScheduler], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "gofer.cli.commands.schedule.time.sleep",
        lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    result = runner.invoke(
        app, ["schedule", "start", "--foreground", "--db", str(tmp_path / "sched.db")]
    )

    assert result.exit_code == 0, result.output
    assert fake_scheduler.started_paths == [tmp_path / "sched.db"]
    assert fake_scheduler.shutdowns == 1
    assert "Scheduler stopped" in result.output


def test_schedule_background_start_running_and_stale_pid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pid_file = data_dir / "scheduler.pid"
    pid_file.write_text("123", encoding="utf-8")
    monkeypatch.setattr(schedule_cmd, "_data_dir", lambda: data_dir)
    monkeypatch.setattr(schedule_cmd, "_is_scheduler", lambda pid: True)

    running = runner.invoke(app, ["schedule", "start", "--db", str(tmp_path / "s.db")])
    assert running.exit_code == 1
    assert "already running" in running.output

    pid_file.write_text("456", encoding="utf-8")

    def stale_kill(pid: int, sig: int) -> None:
        raise ProcessLookupError

    class FakePopen:
        pid = 789

        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired("scheduler", timeout or 0)

    monkeypatch.setattr(schedule_cmd, "_is_scheduler", lambda pid: False)
    monkeypatch.setattr("gofer.cli.commands.schedule.subprocess.Popen", FakePopen)
    stale = runner.invoke(app, ["schedule", "start", "--db", str(tmp_path / "s.db")])

    assert stale.exit_code == 0, stale.output
    assert "PID 789" in stale.output
    assert pid_file.read_text(encoding="utf-8") == "789"


def test_schedule_background_command_runs_the_packaged_cli_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "s.db"
    monkeypatch.delattr(sys, "frozen", raising=False)
    source = schedule_cmd._foreground_command(db)
    assert source[:3] == [sys.executable, "-m", "gofer.cli.main"]

    # The packaged CLI is one executable that rejects -m (No such option: -m).
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    packaged = schedule_cmd._foreground_command(db)
    assert packaged == [sys.executable, "schedule", "start", "--foreground", "--db", str(db)]


def test_schedule_background_start_reports_a_scheduler_that_exits_at_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(schedule_cmd, "_data_dir", lambda: data_dir)

    class ExitedPopen:
        pid = 790

        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            return 2

    monkeypatch.setattr("gofer.cli.commands.schedule.subprocess.Popen", ExitedPopen)
    result = runner.invoke(app, ["schedule", "start", "--db", str(tmp_path / "s.db")])

    assert result.exit_code == 1, result.output
    assert "stopped right after starting (exit 2)" in result.output
    assert not (data_dir / "scheduler.pid").exists()


@pytest.mark.parametrize("content", ["0", "-1", "not a pid", ""])
def test_schedule_stop_never_signals_a_process_group_from_a_bad_pid_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pid_file = data_dir / "scheduler.pid"
    pid_file.write_text(content, encoding="utf-8")
    monkeypatch.setattr(schedule_cmd, "_data_dir", lambda: data_dir)
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "gofer.cli.commands.schedule.os.kill", lambda pid, sig: killed.append((pid, sig))
    )

    result = runner.invoke(app, ["schedule", "stop"])

    # os.kill(0, ...) signals this process group and os.kill(-1, ...) every process.
    assert killed == []
    assert result.exit_code == 1
    assert "unreadable" in result.output
    assert not pid_file.exists()


@pytest.mark.skipif(not Path("/proc/self/cmdline").exists(), reason="reads /proc")
def test_schedule_stop_leaves_a_program_that_reused_the_pid_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(schedule_cmd, "_data_dir", lambda: data_dir)
    other = subprocess.Popen(["sleep", "30"])
    try:
        (data_dir / "scheduler.pid").write_text(str(other.pid), encoding="utf-8")
        result = runner.invoke(app, ["schedule", "stop"])
        assert result.exit_code == 0, result.output
        assert "was not running" in result.output
        assert other.poll() is None
    finally:
        other.kill()
        other.wait()


def test_schedule_stop_missing_running_and_stale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pid_file = data_dir / "scheduler.pid"
    monkeypatch.setattr(schedule_cmd, "_data_dir", lambda: data_dir)

    missing = runner.invoke(app, ["schedule", "stop"])
    assert missing.exit_code == 1
    assert "No background scheduler" in missing.output

    killed: list[tuple[int, int]] = []
    pid_file.write_text("111", encoding="utf-8")
    monkeypatch.setattr(schedule_cmd, "_is_scheduler", lambda pid: True)
    monkeypatch.setattr(
        "gofer.cli.commands.schedule.os.kill",
        lambda pid, sig: killed.append((pid, sig)),
    )
    stopped = runner.invoke(app, ["schedule", "stop"])
    assert stopped.exit_code == 0, stopped.output
    assert killed == [(111, signal.SIGTERM)]
    assert not pid_file.exists()

    pid_file.write_text("222", encoding="utf-8")

    def stale_kill(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr("gofer.cli.commands.schedule.os.kill", stale_kill)
    stale = runner.invoke(app, ["schedule", "stop"])
    assert stale.exit_code == 0, stale.output
    assert "was not running" in stale.output
    assert not pid_file.exists()


def test_schedule_add_invalid_workflow_schedule(
    fake_scheduler: type[_FakeScheduler], tmp_path: Path
) -> None:
    workflow = _create_workflow(tmp_path)

    result = runner.invoke(app, ["schedule", "add", str(workflow), "--db", str(tmp_path / "s.db")])

    assert result.exit_code == 1
    assert "Schedule failed" in result.output
    assert "no schedule" in result.output


def test_schedule_add_displays_external_agent_access(
    fake_scheduler: type[_FakeScheduler], tmp_path: Path
) -> None:
    work_dir = tmp_path / "work"
    extra_dir = tmp_path / "shared"
    work_dir.mkdir()
    extra_dir.mkdir()
    workflow_path = tmp_path / "scheduled.toml"
    workflow_path.write_text(
        f"""
[workflow]
id = "scheduled"
name = "Scheduled"

[workflow.schedule]
cron_expression = "0 9 * * *"

[agents.reviewer]
subscription = "codex"
working_dir = "{work_dir}"
extra_paths = ["{extra_dir}"]
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(
        app, ["schedule", "add", str(workflow_path), "--db", str(tmp_path / "s.db")]
    )

    assert result.exit_code == 0, result.output
    assert "Agent filesystem access outside working_dir" in result.output
    assert "reviewer" in result.output
    assert str(extra_dir) in _unfolded(result.output)


class _FakeWatcher:
    instances: list[_FakeWatcher] = []

    def __init__(self, poll_interval_seconds: float) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self.added: list[str] = []
        self.started = False
        self.shutdown_called = False
        type(self).instances.append(self)

    def add_workflow(self, workflow: AgenticWorkflow, path: Path) -> None:
        if workflow.config.id == "bad-watch":
            raise ValueError("bad watch config")
        self.added.append(workflow.config.id)

    def start(self) -> None:
        self.started = True

    def shutdown(self) -> None:
        self.shutdown_called = True


def test_watch_empty_list_and_invalid_toml_skip(tmp_path: Path) -> None:
    (tmp_path / "bad.toml").write_text("not = [valid", encoding="utf-8")

    result = runner.invoke(app, ["watch", "list", "--data-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "No watched workflows" in result.output


def test_watch_start_no_watched_workflows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _FakeWatcher.instances = []
    _create_workflow(tmp_path)
    monkeypatch.setattr("gofer.core.watcher.WorkflowWatcher", _FakeWatcher)

    result = runner.invoke(app, ["watch", "start", "--data-dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "No watched workflows found" in result.output


def test_watch_start_shutdowns_on_interrupt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _FakeWatcher.instances = []
    (tmp_path / "watched.toml").write_text(
        _SIMPLE_TOML + '\n[workflow.watch]\npath = "inputs"\nglob = "*.txt"\nmode = "batch"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("gofer.core.watcher.WorkflowWatcher", _FakeWatcher)
    monkeypatch.setattr(
        "gofer.cli.commands.watch.time.sleep",
        lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    result = runner.invoke(app, ["watch", "start", "--data-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    watcher = _FakeWatcher.instances[-1]
    assert watcher.started is True
    assert watcher.shutdown_called is True
    assert watcher.added == ["simple"]
    assert "Watcher stopped" in result.output


def test_sync_watchers_skips_invalid_workflows_and_watchers(tmp_path: Path) -> None:
    (tmp_path / "bad.toml").write_text("not = [valid", encoding="utf-8")
    (tmp_path / "ok.toml").write_text(
        _SIMPLE_TOML + '\n[workflow.watch]\npath = "inputs"\nglob = "*.txt"\nmode = "batch"\n',
        encoding="utf-8",
    )
    (tmp_path / "bad-watch.toml").write_text(
        _SIMPLE_TOML.replace('id = "simple"', 'id = "bad-watch"')
        + '\n[workflow.watch]\npath = "inputs"\nglob = "*.txt"\nmode = "batch"\n',
        encoding="utf-8",
    )
    watcher = _FakeWatcher(1.0)

    count = watch_cmd._sync_watchers(tmp_path, cast(Any, watcher))

    assert count == 1
    assert watcher.added == ["simple"]


def test_watch_start_displays_external_agent_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _FakeWatcher.instances = []
    work_dir = tmp_path / "work"
    extra_dir = tmp_path / "shared"
    watch_dir = tmp_path / "inputs"
    work_dir.mkdir()
    extra_dir.mkdir()
    watch_dir.mkdir()
    (tmp_path / "watched.toml").write_text(
        f"""
[workflow]
id = "watched"
name = "Watched"

[workflow.watch]
path = "{watch_dir}"
glob = "*.txt"
mode = "batch"

[agents.reviewer]
subscription = "codex"
working_dir = "{work_dir}"
extra_paths = ["{extra_dir}"]
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr("gofer.core.watcher.WorkflowWatcher", _FakeWatcher)
    monkeypatch.setattr(
        "gofer.cli.commands.watch.time.sleep",
        lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    result = runner.invoke(app, ["watch", "start", "--data-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "Agent filesystem access outside working_dir" in result.output
    assert "reviewer" in result.output
    assert str(extra_dir) in _unfolded(result.output)


def test_workflow_unresolved_id_and_option_parse_errors(tmp_path: Path) -> None:
    _create_workflow(tmp_path)

    missing = runner.invoke(app, ["workflow", "show", "missing", "--data-dir", str(tmp_path)])
    bad_kv = runner.invoke(
        app,
        [
            "workflow",
            "add-node",
            "simple",
            "--id",
            "bad",
            "--type",
            "bash_command",
            "--command",
            "echo hi",
            "--input-map",
            "NOPE",
            "--data-dir",
            str(tmp_path),
        ],
    )
    bad_json = runner.invoke(
        app,
        [
            "workflow",
            "add-node",
            "simple",
            "--id",
            "bad-json",
            "--type",
            "bash_command",
            "--command",
            "echo hi",
            "--input-mapping-json",
            "[]",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert missing.exit_code == 1
    assert "not found" in missing.output
    assert bad_kv.exit_code != 0
    assert "KEY=VALUE" in bad_kv.output
    assert bad_json.exit_code != 0
    assert "JSON object" in bad_json.output


def test_workflow_dry_run_displays_external_agent_access(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    node_work_dir = tmp_path / "node-work"
    extra_dir = work_dir / "workflow-shared-access"
    work_dir.mkdir()
    node_work_dir.mkdir()
    extra_dir.mkdir()
    workflow_path = tmp_path / "agent-access.toml"
    workflow_path.write_text(
        f"""
[workflow]
id = "agent-access"
name = "Agent Access"

[agents.reviewer]
subscription = "codex"
working_dir = "{work_dir}"
extra_paths = ["{extra_dir}"]

[[nodes]]
id = "review"
type = "agent"
agent_id = "reviewer"
working_dir = "{node_work_dir}"
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "workflow",
            "run",
            str(workflow_path),
            "--dry-run",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Agent filesystem access outside working_dir" in result.output
    assert "reviewer" in result.output
    assert "extra_paths entry" in _unfolded(result.output)
    assert node_work_dir.name in _unfolded(result.output)


def test_workflow_rejects_invalid_fan_source_combinations(tmp_path: Path) -> None:
    _create_workflow(tmp_path)

    result = runner.invoke(
        app,
        [
            "workflow",
            "add-node",
            "simple",
            "--id",
            "loop",
            "--type",
            "loop",
            "--fan-source",
            "tabular",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "--fan-path is required" in result.output


def test_workflow_edge_and_node_removal_failures(tmp_path: Path) -> None:
    _create_workflow(tmp_path)

    edge = runner.invoke(
        app,
        [
            "workflow",
            "add-edge",
            "simple",
            "--from",
            "hello",
            "--to",
            "missing",
            "--data-dir",
            str(tmp_path),
        ],
    )
    remove_node = runner.invoke(
        app,
        [
            "workflow",
            "rm-node",
            "simple",
            "--id",
            "missing",
            "--data-dir",
            str(tmp_path),
        ],
    )
    remove_edge = runner.invoke(
        app,
        [
            "workflow",
            "rm-edge",
            "missing",
            "--from",
            "a",
            "--to",
            "b",
            "--data-dir",
            str(tmp_path),
        ],
    )
    missing_edge = runner.invoke(
        app,
        [
            "workflow",
            "rm-edge",
            "simple",
            "--from",
            "hello",
            "--to",
            "missing",
            "--data-dir",
            str(tmp_path),
        ],
    )

    assert edge.exit_code == 1
    assert "Invalid edge config" in edge.output
    assert remove_node.exit_code == 1
    assert "Node 'missing' not found" in remove_node.output
    assert remove_edge.exit_code == 1
    assert "not found" in remove_edge.output
    assert missing_edge.exit_code == 1
    assert "Edge 'hello' -> 'missing' not found" in missing_edge.output


def test_workflow_edit_cancel_save_and_validation_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _create_workflow(tmp_path)
    import gofer.cli.tui_editor as tui_editor

    class CancelEditor:
        def __init__(self, sections: list[Any], title: str) -> None:
            pass

        def run(self) -> bool:
            return False

    monkeypatch.setattr(tui_editor, "FieldEditorApp", CancelEditor)
    cancelled = runner.invoke(app, ["workflow", "edit", "simple", "--data-dir", str(tmp_path)])
    assert cancelled.exit_code == 0, cancelled.output
    assert "Edit cancelled" in cancelled.output

    class SaveEditor:
        def __init__(self, sections: list[Any], title: str) -> None:
            pass

        def run(self) -> bool:
            return True

    monkeypatch.setattr(tui_editor, "FieldEditorApp", SaveEditor)
    saved = runner.invoke(app, ["workflow", "edit", "simple", "--data-dir", str(tmp_path)])
    assert saved.exit_code == 0, saved.output
    assert "Saved" in saved.output

    original_toml = (tmp_path / "simple.toml").read_text(encoding="utf-8")

    def fail_validate(self: AgenticWorkflow, *_args: object) -> None:
        raise ValueError("invalid edit")

    monkeypatch.setattr(AgenticWorkflow, "validate", fail_validate)
    failed = runner.invoke(app, ["workflow", "edit", "simple", "--data-dir", str(tmp_path)])
    assert failed.exit_code == 1
    assert "Validation failed: invalid edit" in failed.output
    assert (tmp_path / "simple.toml").read_text(encoding="utf-8") == original_toml


def test_workflow_delete_confirmation_behavior(tmp_path: Path) -> None:
    _create_workflow(tmp_path, "cancel-delete")
    cancelled = runner.invoke(
        app,
        ["workflow", "rm", "cancel-delete", "--data-dir", str(tmp_path)],
        input="n\n",
    )
    assert cancelled.exit_code != 0
    assert (tmp_path / "cancel-delete.toml").exists()

    _create_workflow(tmp_path, "delete-me")
    chat_path = workflow_chat_prompt_path(tmp_path, "delete-me")
    chat_path.parent.mkdir(parents=True)
    chat_path.write_text("chat", encoding="utf-8")
    deleted = runner.invoke(
        app, ["workflow", "rm", "delete-me", "--yes", "--data-dir", str(tmp_path)]
    )

    assert deleted.exit_code == 0, deleted.output
    assert not (tmp_path / "delete-me.toml").exists()
    assert not chat_path.exists()


def test_workflow_log_error_paths(tmp_path: Path) -> None:
    _create_workflow(tmp_path)

    latest_missing_workflow = runner.invoke(
        app, ["workflow", "logs", "latest", "missing", "--data-dir", str(tmp_path)]
    )
    invalid_run_id = runner.invoke(
        app,
        [
            "workflow",
            "logs",
            "show",
            "simple",
            "../bad.log",
            "--data-dir",
            str(tmp_path),
        ],
    )
    list_empty = runner.invoke(
        app, ["workflow", "logs", "list", "simple", "--data-dir", str(tmp_path)]
    )

    assert latest_missing_workflow.exit_code == 1
    assert "not found" in latest_missing_workflow.output
    assert invalid_run_id.exit_code == 1
    assert "Invalid run log id" in invalid_run_id.output
    assert list_empty.exit_code == 0, list_empty.output
    assert "No run logs found" in list_empty.output


def test_workflow_list_uses_index_and_supports_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_workflow(tmp_path)

    warmed = runner.invoke(app, ["workflow", "list", "--data-dir", str(tmp_path)])
    assert warmed.exit_code == 0, warmed.output

    def fail_parse(path: Path) -> object:
        raise AssertionError(f"uncached workflow parse for {path.name}")

    monkeypatch.setattr(AgenticWorkflow, "from_file", fail_parse)

    result = runner.invoke(
        app,
        ["workflow", "list", "--data-dir", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["workflows"][0]["id"] == "simple"


def test_workflow_run_writes_summary_and_updates_run_index(tmp_path: Path) -> None:
    _create_workflow(tmp_path)

    result = runner.invoke(
        app,
        ["workflow", "run", "simple", "--data-dir", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    [log_path] = (tmp_path / "logs" / "simple").glob("*.log")
    summary_path = log_path.with_suffix(".summary.json")
    index_path = tmp_path / "indexes" / "runs-simple.json"

    assert summary_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["id"] == log_path.name
    assert summary["status"] == "success"

    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert log_path.name in index["runs"]
    assert index["runs"][log_path.name]["summary"]["status"] == "success"


def test_workflow_logs_list_and_prune_support_json(tmp_path: Path) -> None:
    _create_workflow(tmp_path)
    log_dir = tmp_path / "logs" / "simple"
    log_dir.mkdir(parents=True)
    run = log_dir / "2020-01-01T10-00-00-0000.log"
    run.write_text(
        "2020-01-01T10:00:00+00:00 - simple started successfully\n"
        "2020-01-01T10:00:01+00:00 - INFO - simple completed successfully\n",
        encoding="utf-8",
    )

    listed = runner.invoke(
        app,
        ["workflow", "logs", "list", "simple", "--data-dir", str(tmp_path), "--json"],
    )
    pruned = runner.invoke(
        app,
        [
            "workflow",
            "logs",
            "prune",
            "simple",
            "--keep-last",
            "0",
            "--keep-days",
            "0",
            "--data-dir",
            str(tmp_path),
            "--json",
        ],
    )

    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.output)["runs"][0]["id"] == run.name
    assert pruned.exit_code == 0, pruned.output
    prune_payload = json.loads(pruned.output)
    assert prune_payload["dryRun"] is True
    assert prune_payload["runs"][0]["id"] == run.name
