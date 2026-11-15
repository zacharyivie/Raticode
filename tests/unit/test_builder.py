from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from rich.console import Console
from typer.testing import CliRunner

from gofer.cli.commands import builder as builder_mod
from gofer.cli.main import app
from gofer.core.agent import AgentConfig
from gofer.core.graph import EdgeConditionType, GraphNode
from gofer.core.operations import (
    AgentOperation,
    BashCommandOperation,
    CopyFileOperation,
    CountFanSource,
    DeleteFileOperation,
    DirectoryFanSource,
    LoopOperation,
    MoveFileOperation,
    OpenResourceOperation,
    OperationType,
    PythonScriptOperation,
    ReadFileOperation,
    TabularFanSource,
    TriggerEventsFanSource,
    WriteFileOperation,
)
from gofer.core.workflow import AgenticWorkflow, WorkflowConfig

runner = CliRunner()


class _DefaultAnswer:
    pass


DEFAULT = _DefaultAnswer()


class _Prompt:
    def __init__(self, answer: object) -> None:
        self._answer = answer

    def ask(self) -> object:
        return self._answer


class _QuestionaryStub:
    def __init__(self, answers: list[object]) -> None:
        self._answers = answers

    def _next(self, default: object = None) -> object:
        if not self._answers:
            raise AssertionError("Questionary stub ran out of answers")
        answer = self._answers.pop(0)
        return default if answer is DEFAULT else answer

    def text(self, *_args: object, default: object = None, **_kwargs: object) -> _Prompt:
        return _Prompt(self._next(default))

    def confirm(self, *_args: object, default: object = None, **_kwargs: object) -> _Prompt:
        return _Prompt(self._next(default))

    def select(self, *_args: object, default: object = None, **_kwargs: object) -> _Prompt:
        return _Prompt(self._next(default))


def _patch_questionary(monkeypatch: Any, answers: list[object]) -> _QuestionaryStub:
    stub = _QuestionaryStub(answers)
    monkeypatch.setattr(builder_mod, "questionary", stub)
    monkeypatch.setattr(
        builder_mod,
        "console",
        Console(file=io.StringIO(), force_terminal=False, width=100),
    )
    return stub


def _blank_builder() -> builder_mod.WorkflowBuilder:
    builder = builder_mod.WorkflowBuilder()
    builder._workflow = AgenticWorkflow(WorkflowConfig(id="built", name="Built"))
    return builder


def _round_trip(workflow: AgenticWorkflow, path: Path) -> AgenticWorkflow:
    workflow.to_file(path)
    loaded = AgenticWorkflow.from_file(path)
    loaded.validate(path)
    return loaded


def test_builder_creates_metadata_schedule_and_watcher_with_defaulted_numbers(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    _patch_questionary(
        monkeypatch,
        [
            "Nightly Docs",
            DEFAULT,
            True,
            "0 2 * * *",
            "America/New_York",
            True,
            str(tmp_path / "docs"),
            "*.md",
            True,
            "fanout",
            "bad-int",
            "bad-float",
            False,
            False,
            True,
        ],
    )

    workflow = builder_mod.WorkflowBuilder().run()

    assert workflow is not None
    assert workflow.config.id == "nightly-docs"
    assert workflow.config.schedule is not None
    assert workflow.config.schedule.cron_expression == "0 2 * * *"
    assert workflow.config.schedule.timezone == "America/New_York"
    assert workflow.config.watch is not None
    assert workflow.config.watch.glob == "*.md"
    assert workflow.config.watch.recursive is True
    assert workflow.config.watch.mode == "fanout"
    assert workflow.config.watch.max_concurrency == 1
    assert workflow.config.watch.debounce_seconds == 1.0

    loaded = _round_trip(workflow, tmp_path / "nightly-docs.toml")
    assert loaded.config.watch is not None
    assert loaded.config.watch.path == tmp_path / "docs"


def test_builder_cancels_on_required_metadata_prompt(monkeypatch: Any) -> None:
    _patch_questionary(monkeypatch, [None])

    assert builder_mod.WorkflowBuilder().run() is None


def test_builder_save_confirmation_can_cancel(monkeypatch: Any) -> None:
    _patch_questionary(
        monkeypatch,
        ["Draft Flow", DEFAULT, False, False, False, False, False],
    )

    assert builder_mod.WorkflowBuilder().run() is None


def test_builder_adds_command_and_script_nodes(monkeypatch: Any, tmp_path: Path) -> None:
    builder = _blank_builder()
    _patch_questionary(
        monkeypatch,
        ["cmd", "bash_command", "echo hello", str(tmp_path), True],
    )
    builder._ask_one_node()
    _patch_questionary(
        monkeypatch,
        ["py", "python_script", str(tmp_path / "job.py"), "--fast --json", False],
    )
    builder._ask_one_node()

    assert builder._workflow is not None
    command_node = builder._workflow.graph._nodes["cmd"]
    script_node = builder._workflow.graph._nodes["py"]
    assert isinstance(command_node.operation, BashCommandOperation)
    assert command_node.operation.command == "echo hello"
    assert command_node.operation.working_dir == tmp_path
    assert command_node.pipe_output is True
    assert isinstance(script_node.operation, PythonScriptOperation)
    assert script_node.operation.args == ["--fast", "--json"]

    loaded = _round_trip(builder._workflow, tmp_path / "commands.toml")
    assert isinstance(loaded.graph._nodes["py"].operation, PythonScriptOperation)


def test_builder_adds_file_operation_nodes(monkeypatch: Any, tmp_path: Path) -> None:
    builder = _blank_builder()
    node_answers: list[list[object]] = [
        ["read", "read_file", str(tmp_path / "in.txt")],
        ["write", "write_file", str(tmp_path / "out.txt"), "content", True, False, True],
        [
            "copy",
            "copy_file",
            str(tmp_path / "src.txt"),
            str(tmp_path / "copy.txt"),
            True,
            True,
        ],
        [
            "move",
            "move_file",
            str(tmp_path / "copy.txt"),
            str(tmp_path / "moved.txt"),
            False,
            False,
        ],
        ["delete", "delete_file", str(tmp_path / "old.txt"), False, True, True],
    ]
    for answers in node_answers:
        _patch_questionary(monkeypatch, answers)
        builder._ask_one_node()

    assert builder._workflow is not None
    nodes = builder._workflow.graph._nodes
    assert isinstance(nodes["read"].operation, ReadFileOperation)
    assert nodes["read"].pipe_output is True
    assert isinstance(nodes["write"].operation, WriteFileOperation)
    assert nodes["write"].operation.append is True
    assert nodes["write"].operation.overwrite is False
    assert isinstance(nodes["copy"].operation, CopyFileOperation)
    assert nodes["copy"].operation.overwrite is True
    assert isinstance(nodes["move"].operation, MoveFileOperation)
    assert nodes["move"].operation.create_dirs is False
    assert isinstance(nodes["delete"].operation, DeleteFileOperation)
    assert nodes["delete"].operation.recursive is True
    assert nodes["delete"].operation.missing_ok is True

    loaded = _round_trip(builder._workflow, tmp_path / "files.toml")
    assert isinstance(loaded.graph._nodes["delete"].operation, DeleteFileOperation)


def test_builder_adds_open_resource_node(monkeypatch: Any, tmp_path: Path) -> None:
    builder = _blank_builder()
    _patch_questionary(
        monkeypatch,
        ["open", "open_resource", "https://example.com", "url", "--new-window"],
    )

    builder._ask_one_node()

    assert builder._workflow is not None
    op = builder._workflow.graph._nodes["open"].operation
    assert isinstance(op, OpenResourceOperation)
    assert op.target == "https://example.com"
    assert op.resource_type == "url"
    assert op.args == ["--new-window"]

    loaded = _round_trip(builder._workflow, tmp_path / "open.toml")
    assert isinstance(loaded.graph._nodes["open"].operation, OpenResourceOperation)


def test_builder_uses_existing_agent(monkeypatch: Any, tmp_path: Path) -> None:
    existing = AgentConfig(
        agent_id="reviewer",
        subscription="codex",
        prompt_path=tmp_path / "review.md",
        working_dir=tmp_path,
    )
    source_workflow = AgenticWorkflow(WorkflowConfig(id="agents", name="Agents"))
    source_workflow.register_agent(existing)
    monkeypatch.setattr(
        "gofer.utils.registry.list_all_agents", lambda: [(source_workflow, existing)]
    )
    builder = _blank_builder()
    _patch_questionary(
        monkeypatch,
        ["agent", "agent", "existing", "reviewer (agents)", False, True],
    )

    builder._ask_one_node()

    assert builder._workflow is not None
    op = builder._workflow.graph._nodes["agent"].operation
    assert isinstance(op, AgentOperation)
    assert op.agent_id == "reviewer"
    assert op.prompt_path == tmp_path / "review.md"
    assert builder._workflow.graph._nodes["agent"].pipe_output is True
    assert builder._workflow.agents["reviewer"] == existing


def test_builder_falls_back_to_new_agent_and_parses_prompt_tools_and_mcp(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("gofer.utils.registry.list_all_agents", lambda: [])
    monkeypatch.setattr(builder_mod, "get_data_dir", lambda: tmp_path)
    builder = _blank_builder()
    _patch_questionary(
        monkeypatch,
        [
            "agent",
            "agent",
            "existing",
            "Research Agent",
            "codex",
            str(tmp_path),
            "Summarize {{input}}",
            "Read, Write",
            "filesystem, github",
            False,
            True,
        ],
    )

    builder._ask_one_node()

    assert builder._workflow is not None
    op = builder._workflow.graph._nodes["agent"].operation
    assert isinstance(op, AgentOperation)
    assert op.agent_id == "research-agent"
    assert op.prompt_path == tmp_path / "prompts" / "research-agent.md"
    assert op.prompt_path.read_text() == "Summarize {{input}}"
    assert builder._workflow.graph._nodes["agent"].pipe_output is True
    cfg = builder._workflow.agents["research-agent"]
    assert cfg.tools == ["Read", "Write"]
    assert cfg.mcp_servers == ["filesystem", "github"]

    loaded = _round_trip(builder._workflow, tmp_path / "agent.toml")
    assert isinstance(loaded.graph._nodes["agent"].operation, AgentOperation)


def test_builder_creates_fan_out_sources(monkeypatch: Any, tmp_path: Path) -> None:
    builder = _blank_builder()
    cases: list[tuple[str, list[object], type[object]]] = [
        ("count", ["Fixed number of times", "3"], CountFanSource),
        ("table", ["Row in a JSONL/CSV file", str(tmp_path / "rows.jsonl")], TabularFanSource),
        (
            "dir",
            ["File in a directory", str(tmp_path), "*.md", True],
            DirectoryFanSource,
        ),
        ("events", ["File watcher trigger event", True], TriggerEventsFanSource),
    ]
    for node_id, source_answers, expected_type in cases:
        _patch_questionary(monkeypatch, [node_id, "loop", *source_answers])
        builder._ask_one_node()
        assert builder._workflow is not None
        op = builder._workflow.graph._nodes[node_id].operation
        assert isinstance(op, LoopOperation)
        assert isinstance(op.source, expected_type)

    assert builder._workflow is not None
    directory_op = builder._workflow.graph._nodes["dir"].operation
    assert isinstance(directory_op, LoopOperation)
    directory_source = directory_op.source
    assert isinstance(directory_source, DirectoryFanSource)
    assert directory_source.glob == "*.md"
    assert directory_source.include_content is True

    loaded = _round_trip(builder._workflow, tmp_path / "fanout.toml")
    assert isinstance(loaded.graph._nodes["events"].operation, LoopOperation)


def test_builder_edges_cover_conditions_and_reject_self_loop_and_cycle(
    monkeypatch: Any,
) -> None:
    builder = _blank_builder()
    assert builder._workflow is not None
    for node_id in ["a", "b", "c", "d", "e"]:
        builder._workflow.add_operation(
            GraphNode(
                node_id=node_id,
                operation=BashCommandOperation(
                    type=OperationType.BASH_COMMAND,
                    command=f"echo {node_id}",
                ),
            )
        )
    _patch_questionary(
        monkeypatch,
        [
            True,
            "a",
            "b",
            "always",
            True,
            "b",
            "c",
            "on_success",
            True,
            "c",
            "d",
            "on_failure",
            True,
            "d",
            "e",
            "output_matches",
            "READY",
            True,
            "b",
            "b",
            True,
            "c",
            "a",
            False,
        ],
    )

    builder._ask_edges()

    edges = builder._workflow.graph._edges
    assert edges[("a", "b")].condition == EdgeConditionType.ALWAYS
    assert edges[("b", "c")].condition == EdgeConditionType.ON_SUCCESS
    assert edges[("c", "d")].condition == EdgeConditionType.ON_FAILURE
    assert edges[("d", "e")].condition == EdgeConditionType.OUTPUT_MATCHES
    assert edges[("d", "e")].output_pattern == "READY"
    assert ("b", "b") not in edges
    assert ("c", "a") not in edges


def test_builder_authors_named_structured_output_workflow(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    schema = {
        "type": "object",
        "properties": {"score": {"type": "number"}},
        "required": ["score"],
    }
    existing = AgentConfig(
        agent_id="reviewer",
        subscription="codex",
        prompt_path=tmp_path / "review.md",
        working_dir=tmp_path,
    )
    source_workflow = AgenticWorkflow(WorkflowConfig(id="agents", name="Agents"))
    source_workflow.register_agent(existing)
    monkeypatch.setattr(
        "gofer.utils.registry.list_all_agents", lambda: [(source_workflow, existing)]
    )
    builder = _blank_builder()

    _patch_questionary(
        monkeypatch,
        [True, "review-result", json.dumps(schema), False],
    )
    builder._ask_output_schemas()
    _patch_questionary(
        monkeypatch,
        [
            "review",
            "agent",
            "existing",
            "reviewer (agents)",
            True,
            "named schema",
            "review-result",
            "2",
            False,
        ],
    )
    builder._ask_one_node()
    assert builder._workflow is not None
    builder._workflow.add_operation(
        GraphNode(
            node_id="publish",
            operation=BashCommandOperation(
                type=OperationType.BASH_COMMAND, command="echo published"
            ),
        )
    )
    _patch_questionary(
        monkeypatch,
        [
            True,
            "review",
            "publish",
            "output_field",
            "score",
            "greater_than",
            "7",
            False,
        ],
    )
    builder._ask_edges()

    operation = builder._workflow.graph._nodes["review"].operation
    assert isinstance(operation, AgentOperation)
    assert operation.output_schema == "review-result"
    assert operation.repair_attempts == 2
    loaded = _round_trip(builder._workflow, tmp_path / "structured-builder.toml")
    assert loaded.config.output_schemas["review-result"] == schema
    assert loaded.graph._edges[("review", "publish")].value == 7


def test_workflow_build_cli_writes_output(monkeypatch: Any, tmp_path: Path) -> None:
    output = tmp_path / "built.toml"
    _patch_questionary(
        monkeypatch,
        [
            "CLI Flow",
            DEFAULT,
            False,
            False,
            False,
            True,
            "hello",
            "bash_command",
            "echo hi",
            "",
            False,
            False,
            True,
        ],
    )

    result = runner.invoke(app, ["workflow", "build", "--output", str(output)])

    assert result.exit_code == 0, result.output
    workflow = AgenticWorkflow.from_file(output)
    assert workflow.config.id == "cli-flow"
    op = workflow.graph._nodes["hello"].operation
    assert isinstance(op, BashCommandOperation)
    assert op.command == "echo hi"
