"""Unit tests for tui_editor pure functions (no TTY required)."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import questionary
from prompt_toolkit.keys import Keys

from gofer.cli.tui_editor import (
    FieldDescriptor,
    FieldEditorApp,
    FieldKind,
    Section,
    _as_path,
    _as_path_or_none,
    _coerce,
    _format_value,
    agent_to_sections,
    sections_to_agent,
    sections_to_workflow,
    workflow_to_sections,
)
from gofer.core.agent import AgentConfig
from gofer.core.graph import GraphNode
from gofer.core.operations import (
    AgentOperation,
    ApprovalGateOperation,
    BashCommandOperation,
    BreakOperation,
    CommonLlmTaskOperation,
    CopyFileOperation,
    CountFanSource,
    DeleteFileOperation,
    DirectoryFanSource,
    FailOperation,
    FileOperation,
    FolderOperation,
    HttpRequestOperation,
    HttpRetryPolicy,
    LocalSearchOperation,
    LocalVectorizeOperation,
    LoopOperation,
    MoveFileOperation,
    NotificationOperation,
    OpenResourceOperation,
    OperationType,
    PassOperation,
    PromptFileOperation,
    PythonScriptOperation,
    ReadFileOperation,
    ShellScriptOperation,
    StartOperation,
    WriteFileOperation,
)
from gofer.core.workflow import AgenticWorkflow, ScheduleConfig, WatchConfig, WorkflowConfig

# ── Helpers ───────────────────────────────────────────────────────────────────


def _bash_workflow() -> AgenticWorkflow:
    wf = AgenticWorkflow(WorkflowConfig(id="wf1", name="Workflow One"))
    wf.add_operation(
        GraphNode(
            node_id="step1",
            operation=BashCommandOperation(type=OperationType.BASH_COMMAND, command="echo hi"),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="step2",
            operation=BashCommandOperation(
                type=OperationType.BASH_COMMAND,
                command="echo bye",
                working_dir=Path("/tmp"),
                env={"FOO": "bar"},
            ),
            retry_count=2,
            timeout_seconds=30.0,
        )
    )
    wf.then("step1", "step2")
    return wf


def _agent_config() -> AgentConfig:
    return AgentConfig(
        agent_id="myagent",
        subscription="claude_code",
        working_dir=Path("/home/user"),
        prompt_path=Path("/home/user/prompt.md"),
        tools=["bash", "read"],
        mcp_servers=["filesystem"],
        env={"DEBUG": "1"},
    )


class _Prompt:
    def __init__(self, result: object) -> None:
        self._result = result

    def ask(self) -> object:
        return self._result


class _EventApp:
    def __init__(self) -> None:
        self.exited = False

    def exit(self) -> None:
        self.exited = True


def _dispatch(app: FieldEditorApp, key: str | Keys) -> _EventApp:
    event_app = _EventApp()
    event = SimpleNamespace(app=event_app)
    kb = app._build_key_bindings()
    binding = next(binding for binding in kb.bindings if key in binding.keys)
    binding.handler(event)
    return event_app


def _set_field(sections: list[Section], key: str, value: object) -> None:
    field = next(fd for sec in sections for fd in sec.fields if fd.key == key)
    field.value = value


# ── _format_value ─────────────────────────────────────────────────────────────


def test_format_value_none() -> None:
    fd = FieldDescriptor("k", "L", FieldKind.STRING, None, optional=True)
    assert _format_value(fd) == "(none)"


def test_format_value_string() -> None:
    fd = FieldDescriptor("k", "L", FieldKind.STRING, "hello")
    assert _format_value(fd) == "hello"


def test_format_value_bool_true() -> None:
    fd = FieldDescriptor("k", "L", FieldKind.BOOL, True)
    assert _format_value(fd) == "yes"


def test_format_value_bool_false() -> None:
    fd = FieldDescriptor("k", "L", FieldKind.BOOL, False)
    assert _format_value(fd) == "no"


def test_format_value_list_str() -> None:
    fd = FieldDescriptor("k", "L", FieldKind.LIST_STR, ["a", "b", "c"])
    assert _format_value(fd) == "a, b, c"


def test_format_value_list_empty() -> None:
    fd = FieldDescriptor("k", "L", FieldKind.LIST_STR, [])
    assert _format_value(fd) == "(empty)"


def test_format_value_dict() -> None:
    fd = FieldDescriptor("k", "L", FieldKind.DICT_STR_STR, {"X": "1", "Y": "2"})
    assert _format_value(fd) == "X=1, Y=2"


def test_format_value_dict_empty() -> None:
    fd = FieldDescriptor("k", "L", FieldKind.DICT_STR_STR, {})
    assert _format_value(fd) == "(empty)"


# ── _coerce ───────────────────────────────────────────────────────────────────


def test_coerce_int() -> None:
    assert _coerce(FieldKind.INT, "42") == 42


def test_coerce_float() -> None:
    assert _coerce(FieldKind.FLOAT, "3.14") == pytest.approx(3.14)


def test_coerce_path() -> None:
    result = _coerce(FieldKind.PATH, "/tmp/foo")
    assert result == Path("/tmp/foo")


def test_coerce_string() -> None:
    assert _coerce(FieldKind.STRING, "hello") == "hello"


def test_coerce_int_invalid() -> None:
    with pytest.raises(ValueError):
        _coerce(FieldKind.INT, "notanint")


# ── _as_path helpers ──────────────────────────────────────────────────────────


def test_as_path_from_path() -> None:
    p = Path("/a/b")
    assert _as_path(p, Path("/fallback")) == p


def test_as_path_from_string() -> None:
    assert _as_path("/a/b", Path("/fallback")) == Path("/a/b")


def test_as_path_none_returns_fallback() -> None:
    assert _as_path(None, Path("/fallback")) == Path("/fallback")


def test_as_path_or_none_none() -> None:
    assert _as_path_or_none(None) is None


def test_as_path_or_none_empty_string() -> None:
    assert _as_path_or_none("") is None


def test_as_path_or_none_valid() -> None:
    assert _as_path_or_none("/tmp") == Path("/tmp")


# ── workflow_to_sections ─────────────────────────────────────────────────────


def test_workflow_to_sections_structure() -> None:
    wf = _bash_workflow()
    sections = workflow_to_sections(wf)

    assert sections[0].title == "Workflow"
    wf_keys = {fd.key for fd in sections[0].fields}
    assert "config.id" in wf_keys
    assert "config.name" in wf_keys
    assert "config.schedule.cron_expression" in wf_keys

    # Two node sections
    node_titles = {s.title for s in sections[1:]}
    assert any("step1" in t for t in node_titles)
    assert any("step2" in t for t in node_titles)


def test_workflow_to_sections_id_readonly() -> None:
    wf = _bash_workflow()
    sections = workflow_to_sections(wf)
    id_fd = next(fd for fd in sections[0].fields if fd.key == "config.id")
    assert id_fd.read_only is True


def test_workflow_to_sections_schedule_optional() -> None:
    wf = _bash_workflow()
    sections = workflow_to_sections(wf)
    cron_fd = next(fd for fd in sections[0].fields if fd.key == "config.schedule.cron_expression")
    assert cron_fd.optional is True
    assert cron_fd.value is None


def test_workflow_to_sections_with_schedule() -> None:
    wf = AgenticWorkflow(
        WorkflowConfig(
            id="wf2",
            name="Scheduled",
            schedule=ScheduleConfig(cron_expression="0 9 * * *", timezone="US/Eastern"),
        )
    )
    sections = workflow_to_sections(wf)
    cron_fd = next(fd for fd in sections[0].fields if fd.key == "config.schedule.cron_expression")
    assert cron_fd.value == "0 9 * * *"


def test_workflow_to_sections_bash_fields() -> None:
    wf = _bash_workflow()
    sections = workflow_to_sections(wf)
    step2_sec = next(s for s in sections if "step2" in s.title)
    step2_keys = {fd.key for fd in step2_sec.fields}
    assert "nodes.step2.command" in step2_keys
    assert "nodes.step2.retry_count" in step2_keys
    assert "nodes.step2.timeout_seconds" in step2_keys


def test_workflow_to_sections_node_values() -> None:
    wf = _bash_workflow()
    sections = workflow_to_sections(wf)
    step2_sec = next(s for s in sections if "step2" in s.title)
    fm = {fd.key: fd.value for fd in step2_sec.fields}
    assert fm["nodes.step2.command"] == "echo bye"
    assert fm["nodes.step2.retry_count"] == 2
    assert fm["nodes.step2.timeout_seconds"] == 30.0
    assert fm["nodes.step2.env"] == {"FOO": "bar"}


# ── sections_to_workflow ──────────────────────────────────────────────────────


def test_sections_to_workflow_name_change() -> None:
    wf = _bash_workflow()
    sections = workflow_to_sections(wf)
    name_fd = next(fd for fd in sections[0].fields if fd.key == "config.name")
    name_fd.value = "Updated Name"
    sections_to_workflow(sections, wf)
    assert wf.config.name == "Updated Name"


def test_sections_to_workflow_adds_schedule() -> None:
    wf = _bash_workflow()
    sections = workflow_to_sections(wf)
    cron_fd = next(fd for fd in sections[0].fields if fd.key == "config.schedule.cron_expression")
    cron_fd.value = "0 8 * * 1"
    sections_to_workflow(sections, wf)
    assert wf.config.schedule is not None
    assert wf.config.schedule.cron_expression == "0 8 * * 1"


def test_sections_to_workflow_clears_schedule() -> None:
    wf = AgenticWorkflow(
        WorkflowConfig(
            id="wf3",
            name="Sched",
            schedule=ScheduleConfig(cron_expression="0 9 * * *"),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="n1",
            operation=BashCommandOperation(type=OperationType.BASH_COMMAND, command="echo"),
        )
    )
    sections = workflow_to_sections(wf)
    cron_fd = next(fd for fd in sections[0].fields if fd.key == "config.schedule.cron_expression")
    cron_fd.value = None
    sections_to_workflow(sections, wf)
    assert wf.config.schedule is None


def test_sections_to_workflow_updates_bash_command() -> None:
    wf = _bash_workflow()
    sections = workflow_to_sections(wf)
    step1_sec = next(s for s in sections if "step1" in s.title)
    cmd_fd = next(fd for fd in step1_sec.fields if fd.key == "nodes.step1.command")
    cmd_fd.value = "echo updated"
    sections_to_workflow(sections, wf)
    node = wf.graph._nodes["step1"]
    assert isinstance(node.operation, BashCommandOperation)
    assert node.operation.command == "echo updated"


def test_sections_to_workflow_updates_retry_count() -> None:
    wf = _bash_workflow()
    sections = workflow_to_sections(wf)
    step2_sec = next(s for s in sections if "step2" in s.title)
    rc_fd = next(fd for fd in step2_sec.fields if fd.key == "nodes.step2.retry_count")
    rc_fd.value = 5
    sections_to_workflow(sections, wf)
    assert wf.graph._nodes["step2"].retry_count == 5


def test_workflow_to_sections_python_script() -> None:
    wf = AgenticWorkflow(WorkflowConfig(id="py", name="Py"))
    wf.add_operation(
        GraphNode(
            node_id="pyscript",
            operation=PythonScriptOperation(
                type=OperationType.PYTHON_SCRIPT,
                script_path=Path("/scripts/run.py"),
                args=["--verbose"],
            ),
        )
    )
    sections = workflow_to_sections(wf)
    node_sec = next(s for s in sections if "pyscript" in s.title)
    keys = {fd.key for fd in node_sec.fields}
    assert "nodes.pyscript.script_path" in keys
    assert "nodes.pyscript.args" in keys


def test_workflow_to_sections_shell_script() -> None:
    wf = AgenticWorkflow(WorkflowConfig(id="sh", name="Sh"))
    wf.add_operation(
        GraphNode(
            node_id="shscript",
            operation=ShellScriptOperation(
                type=OperationType.SHELL_SCRIPT,
                script_path=Path("/scripts/run.sh"),
            ),
        )
    )
    sections = workflow_to_sections(wf)
    node_sec = next(s for s in sections if "shscript" in s.title)
    keys = {fd.key for fd in node_sec.fields}
    assert "nodes.shscript.script_path" in keys


def test_workflow_to_sections_agent_operation() -> None:
    wf = AgenticWorkflow(WorkflowConfig(id="ag", name="Ag"))
    wf.add_operation(
        GraphNode(
            node_id="agent_node",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="myagent",
                prompt_path=Path("/prompts/p.md"),
                working_dir=Path("/work"),
                dynamic_count=3,
            ),
        )
    )
    sections = workflow_to_sections(wf)
    node_sec = next(s for s in sections if "agent_node" in s.title)
    keys = {fd.key for fd in node_sec.fields}
    assert "nodes.agent_node.agent_id" in keys
    assert "nodes.agent_node.prompt_path" in keys
    assert "nodes.agent_node.dynamic_count" in keys


def test_workflow_to_sections_http_request_includes_json_and_retry_controls() -> None:
    wf = AgenticWorkflow(WorkflowConfig(id="http", name="HTTP"))
    wf.add_operation(
        GraphNode(
            node_id="api",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                method="POST",
                url="https://api.example.test/issues",
                json={"title": "Bug"},
                retry=HttpRetryPolicy(
                    attempts=3,
                    backoff_seconds=1.5,
                    retry_on_statuses=[429, 503],
                ),
            ),
        )
    )

    sections = workflow_to_sections(wf)
    node_sec = next(s for s in sections if "api" in s.title)
    fm = {fd.key: fd.value for fd in node_sec.fields}

    assert '"title": "Bug"' in fm["nodes.api.json_payload"]
    assert fm["nodes.api.retry.attempts"] == 3
    assert fm["nodes.api.retry.backoff_seconds"] == 1.5
    assert fm["nodes.api.retry.retry_on_statuses"] == ["429", "503"]


def test_sections_to_workflow_updates_http_json_and_retry_policy() -> None:
    wf = AgenticWorkflow(WorkflowConfig(id="http", name="HTTP"))
    wf.add_operation(
        GraphNode(
            node_id="api",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                url="https://api.example.test/issues",
            ),
        )
    )
    sections = workflow_to_sections(wf)
    node_sec = next(s for s in sections if "api" in s.title)
    fields = {fd.key: fd for fd in node_sec.fields}
    fields["nodes.api.json_payload"].value = '{"title": "{{previous.output}}"}'
    fields["nodes.api.retry.attempts"].value = 4
    fields["nodes.api.retry.backoff_seconds"].value = 2.0
    fields["nodes.api.retry.retry_on_statuses"].value = ["429", "503"]

    sections_to_workflow(sections, wf)
    op = wf.graph._nodes["api"].operation

    assert isinstance(op, HttpRequestOperation)
    assert op.json_payload == {"title": "{{previous.output}}"}
    assert op.retry.attempts == 4
    assert op.retry.backoff_seconds == 2.0
    assert op.retry.retry_on_statuses == [429, 503]


def test_sections_to_workflow_updates_file_operation_branches() -> None:
    wf = AgenticWorkflow(WorkflowConfig(id="files", name="Files"))
    wf.add_operation(
        GraphNode(
            node_id="read",
            operation=ReadFileOperation(
                type=OperationType.READ_FILE,
                path=Path("/old/read.txt"),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="write",
            operation=WriteFileOperation(
                type=OperationType.WRITE_FILE,
                path=Path("/old/write.txt"),
                content="old",
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="copy",
            operation=CopyFileOperation(
                type=OperationType.COPY_FILE,
                source_path=Path("/old/source.txt"),
                destination_path=Path("/old/dest.txt"),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="move",
            operation=MoveFileOperation(
                type=OperationType.MOVE_FILE,
                source_path=Path("/old/move-source.txt"),
                destination_path=Path("/old/move-dest.txt"),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="delete",
            operation=DeleteFileOperation(
                type=OperationType.DELETE_FILE,
                path=Path("/old/delete.txt"),
            ),
        )
    )

    sections = workflow_to_sections(wf)
    _set_field(sections, "nodes.read.path", Path("/new/read.txt"))
    _set_field(sections, "nodes.read.encoding", "utf-16")
    _set_field(sections, "nodes.read.errors", "ignore")
    _set_field(sections, "nodes.write.path", Path("/new/write.txt"))
    _set_field(sections, "nodes.write.content", "new content")
    _set_field(sections, "nodes.write.create_dirs", False)
    _set_field(sections, "nodes.write.overwrite", False)
    _set_field(sections, "nodes.write.append", True)
    _set_field(sections, "nodes.copy.source_path", Path("/new/copy-source.txt"))
    _set_field(sections, "nodes.copy.destination_path", Path("/new/copy-dest.txt"))
    _set_field(sections, "nodes.copy.create_dirs", False)
    _set_field(sections, "nodes.copy.overwrite", True)
    _set_field(sections, "nodes.move.source_path", Path("/new/move-source.txt"))
    _set_field(sections, "nodes.move.destination_path", Path("/new/move-dest.txt"))
    _set_field(sections, "nodes.move.create_dirs", False)
    _set_field(sections, "nodes.move.overwrite", True)
    _set_field(sections, "nodes.delete.path", Path("/new/delete.txt"))
    _set_field(sections, "nodes.delete.use_trash", False)
    _set_field(sections, "nodes.delete.recursive", True)
    _set_field(sections, "nodes.delete.missing_ok", True)

    sections_to_workflow(sections, wf)

    read_op = wf.graph._nodes["read"].operation
    write_op = wf.graph._nodes["write"].operation
    copy_op = wf.graph._nodes["copy"].operation
    move_op = wf.graph._nodes["move"].operation
    delete_op = wf.graph._nodes["delete"].operation
    assert isinstance(read_op, ReadFileOperation)
    assert read_op.path == Path("/new/read.txt")
    assert read_op.encoding == "utf-16"
    assert read_op.errors == "ignore"
    assert isinstance(write_op, WriteFileOperation)
    assert write_op.path == Path("/new/write.txt")
    assert write_op.content == "new content"
    assert write_op.create_dirs is False
    assert write_op.overwrite is False
    assert write_op.append is True
    assert isinstance(copy_op, CopyFileOperation)
    assert copy_op.source_path == Path("/new/copy-source.txt")
    assert copy_op.destination_path == Path("/new/copy-dest.txt")
    assert copy_op.create_dirs is False
    assert copy_op.overwrite is True
    assert isinstance(move_op, MoveFileOperation)
    assert move_op.source_path == Path("/new/move-source.txt")
    assert move_op.destination_path == Path("/new/move-dest.txt")
    assert move_op.create_dirs is False
    assert move_op.overwrite is True
    assert isinstance(delete_op, DeleteFileOperation)
    assert delete_op.path == Path("/new/delete.txt")
    assert delete_op.use_trash is False
    assert delete_op.recursive is True
    assert delete_op.missing_ok is True


def test_sections_to_workflow_updates_open_resource_and_agent_branches() -> None:
    wf = AgenticWorkflow(WorkflowConfig(id="ops", name="Ops"))
    wf.add_operation(
        GraphNode(
            node_id="open",
            operation=OpenResourceOperation(
                type=OperationType.OPEN_RESOURCE,
                target="https://old.example.test",
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="old-agent",
                prompt_path=Path("/old/prompt.md"),
                working_dir=Path("/old/work"),
                dynamic_count=1,
            ),
            timeout_seconds=20.0,
        )
    )

    sections = workflow_to_sections(wf)
    _set_field(sections, "nodes.open.target", "/tmp/report.txt")
    _set_field(sections, "nodes.open.resource_type", "file")
    _set_field(sections, "nodes.open.args", ["--line", "10"])
    _set_field(sections, "nodes.agent.agent_id", "new-agent")
    _set_field(sections, "nodes.agent.prompt_path", Path("/new/prompt.md"))
    _set_field(sections, "nodes.agent.working_dir", Path("/new/work"))
    _set_field(sections, "nodes.agent.dynamic_count", "{{fanout.count}}")
    _set_field(sections, "nodes.agent.memory", "run")
    _set_field(sections, "nodes.agent.input_mapping", {"source": "read.output"})
    _set_field(sections, "nodes.agent.timeout_seconds", 45.0)
    _set_field(sections, "nodes.agent.pipe_output", True)

    sections_to_workflow(sections, wf)

    open_op = wf.graph._nodes["open"].operation
    agent_node = wf.graph._nodes["agent"]
    agent_op = agent_node.operation
    assert isinstance(open_op, OpenResourceOperation)
    assert open_op.target == "/tmp/report.txt"
    assert open_op.resource_type == "file"
    assert open_op.args == ["--line", "10"]
    assert isinstance(agent_op, AgentOperation)
    assert agent_op.agent_id == "new-agent"
    assert agent_op.prompt_path == Path("/new/prompt.md")
    assert agent_op.working_dir == Path("/new/work")
    assert str(agent_op.dynamic_count) == "{{fanout.count}}"
    assert agent_op.memory == "run"
    assert agent_op.input_mapping == {"source": "read.output"}
    assert agent_node.timeout_seconds == 45.0
    assert agent_node.pipe_output is True


def test_sections_to_workflow_updates_schedule_watch_and_max_node_runs() -> None:
    wf = AgenticWorkflow(
        WorkflowConfig(
            id="triggered",
            name="Triggered",
            schedule=ScheduleConfig(cron_expression="0 9 * * *"),
            watch=WatchConfig(path=Path("/old/watch")),
            max_total_node_runs=10,
        )
    )

    sections = workflow_to_sections(wf)
    _set_field(sections, "config.max_total_node_runs", 250)
    _set_field(sections, "config.schedule.cron_expression", "*/5 * * * *")
    _set_field(sections, "config.schedule.timezone", "America/New_York")
    _set_field(sections, "config.watch.path", Path("/new/watch"))
    _set_field(sections, "config.watch.glob", "*.py")
    _set_field(sections, "config.watch.recursive", True)
    _set_field(sections, "config.watch.debounce_seconds", 2.5)
    _set_field(sections, "config.watch.mode", "queue")
    _set_field(sections, "config.watch.max_concurrency", 3)

    sections_to_workflow(sections, wf)

    assert wf.config.max_total_node_runs == 250
    assert wf.config.schedule is not None
    assert wf.config.schedule.cron_expression == "*/5 * * * *"
    assert wf.config.schedule.timezone == "America/New_York"
    assert wf.config.watch is not None
    assert wf.config.watch.path == Path("/new/watch")
    assert wf.config.watch.glob == "*.py"
    assert wf.config.watch.recursive is True
    assert wf.config.watch.debounce_seconds == 2.5
    assert wf.config.watch.mode == "queue"
    assert wf.config.watch.max_concurrency == 3


def test_sections_to_workflow_updates_control_and_loop_operation_branches() -> None:
    wf = AgenticWorkflow(WorkflowConfig(id="control", name="Control"))
    wf.add_operation(GraphNode(node_id="start", operation=StartOperation(type=OperationType.START)))
    wf.add_operation(
        GraphNode(
            node_id="pass",
            operation=PassOperation(type=OperationType.PASS, message="old pass"),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="fail",
            operation=FailOperation(type=OperationType.FAIL, message="old fail"),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="break",
            operation=BreakOperation(type=OperationType.BREAK, message="old break"),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="loop",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=CountFanSource(type="count", count=2),
            ),
        )
    )

    sections = workflow_to_sections(wf)
    _set_field(sections, "nodes.pass.message", "new pass")
    _set_field(sections, "nodes.fail.message", "new fail")
    _set_field(sections, "nodes.break.message", "new break")
    _set_field(sections, "nodes.loop.source.type", "directory")
    _set_field(sections, "nodes.loop.source.max_concurrency", 4)
    _set_field(sections, "nodes.loop.source.fail_fast", True)

    loop_section = next(section for section in sections if "loop" in section.title)
    loop_section.fields.extend(
        [
            FieldDescriptor(
                "nodes.loop.source.path",
                "Path",
                FieldKind.PATH,
                Path("/data/events"),
            ),
            FieldDescriptor("nodes.loop.source.glob", "Glob", FieldKind.STRING, "*.json"),
            FieldDescriptor(
                "nodes.loop.source.include_content",
                "Include Content",
                FieldKind.BOOL,
                True,
            ),
        ]
    )

    sections_to_workflow(sections, wf)

    assert isinstance(wf.graph._nodes["start"].operation, StartOperation)
    pass_op = wf.graph._nodes["pass"].operation
    fail_op = wf.graph._nodes["fail"].operation
    break_op = wf.graph._nodes["break"].operation
    loop_op = wf.graph._nodes["loop"].operation
    assert isinstance(pass_op, PassOperation)
    assert pass_op.message == "new pass"
    assert isinstance(fail_op, FailOperation)
    assert fail_op.message == "new fail"
    assert isinstance(break_op, BreakOperation)
    assert break_op.message == "new break"
    assert isinstance(loop_op, LoopOperation)
    assert isinstance(loop_op.source, DirectoryFanSource)
    assert loop_op.source.path == Path("/data/events")
    assert loop_op.source.glob == "*.json"
    assert loop_op.source.include_content is True
    assert loop_op.source.max_concurrency == 4
    assert loop_op.source.fail_fast is True


def test_sections_to_workflow_updates_more_file_and_index_operation_branches() -> None:
    wf = AgenticWorkflow(WorkflowConfig(id="more-files", name="More Files"))
    wf.add_operation(
        GraphNode(
            node_id="file",
            operation=FileOperation(type=OperationType.FILE, path=Path("/old/file.txt")),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="folder",
            operation=FolderOperation(type=OperationType.FOLDER, path=Path("/old/folder")),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="prompt",
            operation=PromptFileOperation(
                type=OperationType.PROMPT_FILE,
                output_path=Path("/old/prompt.md"),
                template="old",
                template_path=Path("/old/template.md"),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="vectorize",
            operation=LocalVectorizeOperation(
                type=OperationType.LOCAL_VECTORIZE,
                source_path=Path("/old/src"),
                index_path=Path("/old/index"),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="search",
            operation=LocalSearchOperation(
                type=OperationType.LOCAL_SEARCH,
                index_path=Path("/old/index"),
                query="old",
            ),
        )
    )

    sections = workflow_to_sections(wf)
    _set_field(sections, "nodes.file.path", Path("/new/file.txt"))
    _set_field(sections, "nodes.folder.path", Path("/new/folder"))
    _set_field(sections, "nodes.prompt.output_path", Path("/new/prompt.md"))
    _set_field(sections, "nodes.prompt.template", "new template")
    _set_field(sections, "nodes.prompt.template_path", None)
    _set_field(sections, "nodes.prompt.variables", {"name": "Ada"})
    _set_field(sections, "nodes.prompt.encoding", "utf-16")
    _set_field(sections, "nodes.prompt.create_dirs", False)
    _set_field(sections, "nodes.prompt.overwrite", False)
    _set_field(sections, "nodes.vectorize.source_path", Path("/new/src"))
    _set_field(sections, "nodes.vectorize.index_path", Path("/new/index"))
    _set_field(sections, "nodes.vectorize.glob", "*.md")
    _set_field(sections, "nodes.vectorize.recursive", False)
    _set_field(sections, "nodes.vectorize.chunk_size", 400)
    _set_field(sections, "nodes.vectorize.chunk_overlap", 40)
    _set_field(sections, "nodes.vectorize.mode", "full")
    _set_field(sections, "nodes.search.index_path", Path("/new/index"))
    _set_field(sections, "nodes.search.query", "new query")
    _set_field(sections, "nodes.search.top_k", 8)
    _set_field(sections, "nodes.search.score_threshold", 0.25)
    _set_field(sections, "nodes.search.include_snippets", False)
    _set_field(sections, "nodes.search.include_file_metadata", False)

    sections_to_workflow(sections, wf)

    file_op = wf.graph._nodes["file"].operation
    folder_op = wf.graph._nodes["folder"].operation
    prompt_op = wf.graph._nodes["prompt"].operation
    vectorize_op = wf.graph._nodes["vectorize"].operation
    search_op = wf.graph._nodes["search"].operation
    assert isinstance(file_op, FileOperation)
    assert file_op.path == Path("/new/file.txt")
    assert isinstance(folder_op, FolderOperation)
    assert folder_op.path == Path("/new/folder")
    assert isinstance(prompt_op, PromptFileOperation)
    assert prompt_op.output_path == Path("/new/prompt.md")
    assert prompt_op.template == "new template"
    assert prompt_op.template_path is None
    assert prompt_op.variables == {"name": "Ada"}
    assert prompt_op.encoding == "utf-16"
    assert prompt_op.create_dirs is False
    assert prompt_op.overwrite is False
    assert isinstance(vectorize_op, LocalVectorizeOperation)
    assert vectorize_op.source_path == Path("/new/src")
    assert vectorize_op.index_path == Path("/new/index")
    assert vectorize_op.glob == "*.md"
    assert vectorize_op.recursive is False
    assert vectorize_op.chunk_size == 400
    assert vectorize_op.chunk_overlap == 40
    assert vectorize_op.mode == "full"
    assert isinstance(search_op, LocalSearchOperation)
    assert search_op.index_path == Path("/new/index")
    assert search_op.query == "new query"
    assert search_op.top_k == 8
    assert search_op.score_threshold == 0.25
    assert search_op.include_snippets is False
    assert search_op.include_file_metadata is False


def test_sections_to_workflow_updates_llm_approval_and_notification_branches() -> None:
    wf = AgenticWorkflow(WorkflowConfig(id="advanced", name="Advanced"))
    wf.add_operation(
        GraphNode(
            node_id="common",
            operation=CommonLlmTaskOperation(
                type=OperationType.COMMON_LLM_TASK,
                agent_id="old-agent",
                target="old target",
                working_dir=Path("/old/work"),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="approval",
            operation=ApprovalGateOperation(
                type=OperationType.APPROVAL_GATE,
                message="old approval",
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="notify",
            operation=NotificationOperation(
                type=OperationType.NOTIFICATION,
                title="old title",
                body="old body",
            ),
        )
    )

    sections = workflow_to_sections(wf)
    _set_field(sections, "nodes.common.agent_id", "new-agent")
    _set_field(sections, "nodes.common.task", "review")
    _set_field(sections, "nodes.common.target", "{{build.output}}")
    _set_field(sections, "nodes.common.instructions", "be terse")
    _set_field(sections, "nodes.common.working_dir", Path("/new/work"))
    _set_field(sections, "nodes.common.profile", "prod")
    _set_field(sections, "nodes.common.model", "gpt-test")
    _set_field(sections, "nodes.common.timeout", 12.5)
    _set_field(sections, "nodes.common.memory", "run")
    _set_field(sections, "nodes.common.input_mapping", {"diff": "build.output"})
    _set_field(sections, "nodes.approval.message", "new approval")
    _set_field(sections, "nodes.approval.approval_timeout_seconds", 30.0)
    _set_field(sections, "nodes.approval.timeout_decision", "reject")
    _set_field(sections, "nodes.approval.approvers", ["ops", "security"])
    _set_field(sections, "nodes.approval.notify", True)
    _set_field(sections, "nodes.approval.notification_title", "Approve deploy")
    _set_field(sections, "nodes.notify.title", "new title")
    _set_field(sections, "nodes.notify.body", "new body")
    _set_field(sections, "nodes.notify.urgency", "critical")

    sections_to_workflow(sections, wf)

    common_op = wf.graph._nodes["common"].operation
    approval_op = wf.graph._nodes["approval"].operation
    notify_op = wf.graph._nodes["notify"].operation
    assert isinstance(common_op, CommonLlmTaskOperation)
    assert common_op.agent_id == "new-agent"
    assert common_op.task == "review"
    assert common_op.target == "{{build.output}}"
    assert common_op.instructions == "be terse"
    assert common_op.working_dir == Path("/new/work")
    assert common_op.profile == "prod"
    assert common_op.model == "gpt-test"
    assert common_op.timeout == 12.5
    assert common_op.memory == "run"
    assert common_op.input_mapping == {"diff": "build.output"}
    assert isinstance(approval_op, ApprovalGateOperation)
    assert approval_op.message == "new approval"
    assert approval_op.timeout_seconds == 30.0
    assert approval_op.timeout_decision == "reject"
    assert approval_op.approvers == ["ops", "security"]
    assert approval_op.notify is True
    assert approval_op.notification_title == "Approve deploy"
    assert isinstance(notify_op, NotificationOperation)
    assert notify_op.title == "new title"
    assert notify_op.body == "new body"
    assert notify_op.urgency == "critical"


def test_workflow_sections_expose_and_preserve_notification_channel_config() -> None:
    wf = AgenticWorkflow(WorkflowConfig(id="notify", name="Notify"))
    wf.add_operation(
        GraphNode(
            node_id="notify",
            operation=NotificationOperation(
                type=OperationType.NOTIFICATION,
                title="Deploy",
                body="Done",
                channel="slack",
                webhook_url="{{secret.SLACK_WEBHOOK_URL}}",
                headers={"Authorization": "{{secret.API_TOKEN}}"},
                payload={"text": "{{deploy.output}}"},
                email_from="gofer@example.test",
                email_to=["ops@example.test"],
                smtp_host="smtp.example.test",
                smtp_port=2525,
                smtp_username="{{secret.SMTP_USER}}",
                smtp_password="{{secret.SMTP_PASSWORD}}",
                smtp_starttls=False,
                timeout_seconds=12.5,
                retry=HttpRetryPolicy(
                    attempts=3,
                    backoff_seconds=1.5,
                    retry_on_statuses=[429, 503],
                ),
                expected_statuses=[200, 202],
                network_allowlist=["10.0.0.0/8"],
            ),
        )
    )

    sections = workflow_to_sections(wf)
    node_sec = next(s for s in sections if "notify" in s.title)
    fm = {fd.key: fd.value for fd in node_sec.fields}

    assert fm["nodes.notify.channel"] == "slack"
    channel_field = next(fd for fd in node_sec.fields if fd.key == "nodes.notify.channel")
    assert channel_field.choices == ["desktop", "slack", "teams", "webhook", "email"]
    assert fm["nodes.notify.webhook_url"] == "{{secret.SLACK_WEBHOOK_URL}}"
    assert fm["nodes.notify.headers"] == {"Authorization": "{{secret.API_TOKEN}}"}
    assert '"text": "{{deploy.output}}"' in fm["nodes.notify.payload"]
    assert fm["nodes.notify.smtp_starttls"] is False
    assert fm["nodes.notify.retry.retry_on_statuses"] == ["429", "503"]

    _set_field(sections, "nodes.notify.title", "Deploy finished")
    _set_field(sections, "nodes.notify.payload", '{"text": "updated"}')
    sections_to_workflow(sections, wf)
    op = wf.graph._nodes["notify"].operation

    assert isinstance(op, NotificationOperation)
    assert op.title == "Deploy finished"
    assert op.channel == "slack"
    assert op.webhook_url == "{{secret.SLACK_WEBHOOK_URL}}"
    assert op.headers == {"Authorization": "{{secret.API_TOKEN}}"}
    assert op.payload == {"text": "updated"}
    assert op.smtp_password == "{{secret.SMTP_PASSWORD}}"
    assert op.smtp_starttls is False
    assert op.retry.retry_on_statuses == [429, 503]
    assert op.network_allowlist == ["10.0.0.0/8"]


# ── agent_to_sections / sections_to_agent ────────────────────────────────────


def test_agent_to_sections_structure() -> None:
    cfg = _agent_config()
    sections = agent_to_sections(cfg)
    assert len(sections) == 1
    keys = {fd.key for fd in sections[0].fields}
    assert "agent.agent_id" in keys
    assert "agent.subscription" in keys
    assert "agent.tools" in keys
    assert "agent.env" in keys


def test_agent_to_sections_agent_id_readonly() -> None:
    cfg = _agent_config()
    sections = agent_to_sections(cfg)
    id_fd = next(fd for fd in sections[0].fields if fd.key == "agent.agent_id")
    assert id_fd.read_only is True


def test_agent_to_sections_values() -> None:
    cfg = _agent_config()
    sections = agent_to_sections(cfg)
    fm = {fd.key: fd.value for fd in sections[0].fields}
    assert fm["agent.subscription"] == "claude_code"
    assert fm["agent.tools"] == ["bash", "read"]
    assert fm["agent.env"] == {"DEBUG": "1"}


def test_sections_to_agent_roundtrip() -> None:
    cfg = _agent_config()
    sections = agent_to_sections(cfg)
    result = sections_to_agent(sections, cfg)
    assert result.subscription == cfg.subscription
    assert result.working_dir == cfg.working_dir
    assert result.tools == cfg.tools
    assert result.env == cfg.env


def test_sections_to_agent_updates_subscription() -> None:
    cfg = _agent_config()
    sections = agent_to_sections(cfg)
    sub_fd = next(fd for fd in sections[0].fields if fd.key == "agent.subscription")
    sub_fd.value = "codex"
    result = sections_to_agent(sections, cfg)
    assert result.subscription == "codex"


def test_sections_to_agent_updates_tools() -> None:
    cfg = _agent_config()
    sections = agent_to_sections(cfg)
    tools_fd = next(fd for fd in sections[0].fields if fd.key == "agent.tools")
    tools_fd.value = ["bash", "read", "write"]
    result = sections_to_agent(sections, cfg)
    assert result.tools == ["bash", "read", "write"]


# ── FieldEditorApp construction ───────────────────────────────────────────────


def test_field_editor_flat_count() -> None:
    sections = [
        Section(
            "A",
            [
                FieldDescriptor("a.x", "X", FieldKind.STRING, "foo"),
                FieldDescriptor("a.y", "Y", FieldKind.INT, 1),
            ],
        ),
        Section(
            "B",
            [
                FieldDescriptor("b.z", "Z", FieldKind.BOOL, True),
            ],
        ),
    ]
    app = FieldEditorApp(sections, title="Test")
    assert len(app._flat) == 3


def test_field_editor_initial_cursor() -> None:
    sections = [Section("A", [FieldDescriptor("k", "L", FieldKind.STRING, "v")])]
    app = FieldEditorApp(sections, title="T")
    assert app._cursor == 0
    assert app._saved is False


def test_field_editor_key_bindings_navigate_save_and_quit() -> None:
    app = FieldEditorApp(
        [
            Section(
                "A",
                [
                    FieldDescriptor("a", "A", FieldKind.STRING, "a"),
                    FieldDescriptor("b", "B", FieldKind.STRING, "b"),
                    FieldDescriptor("c", "C", FieldKind.STRING, "c"),
                ],
            )
        ]
    )

    _dispatch(app, "j")
    assert app._cursor == 1
    _dispatch(app, Keys.Up)
    assert app._cursor == 0
    _dispatch(app, Keys.PageDown)
    assert app._cursor == 2
    _dispatch(app, Keys.PageUp)
    assert app._cursor == 0

    saved_event = _dispatch(app, "s")
    assert app._saved is True
    assert saved_event.exited is True

    quit_event = _dispatch(app, "q")
    assert app._saved is False
    assert quit_event.exited is True


def test_field_editor_run_edit_loop_saves_without_real_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    field = FieldDescriptor("name", "Name", FieldKind.STRING, "old")
    app = FieldEditorApp([Section("A", [field])])
    keys = iter([Keys.ControlM, "s"])

    class FakeApplication:
        def __init__(self, **kwargs: object) -> None:
            self.key_bindings = kwargs["key_bindings"]

        def run(self) -> None:
            key = next(keys)
            event_app = _EventApp()
            event = SimpleNamespace(app=event_app)
            binding = next(binding for binding in self.key_bindings.bindings if key in binding.keys)
            binding.handler(event)

    monkeypatch.setattr("gofer.cli.tui_editor.Application", FakeApplication)
    monkeypatch.setattr(questionary, "text", lambda *_, **__: _Prompt("new"))

    assert app.run() is True
    assert field.value == "new"


def test_field_editor_enter_delete_read_only_and_required_errors() -> None:
    readonly = FieldDescriptor("id", "ID", FieldKind.STRING, "wf", read_only=True)
    required = FieldDescriptor("name", "Name", FieldKind.STRING, "old")
    optional = FieldDescriptor("note", "Note", FieldKind.STRING, "text", optional=True)
    app = FieldEditorApp([Section("A", [readonly, required, optional])])
    app._pending_edit = None

    readonly_event = _dispatch(app, Keys.ControlM)
    assert app._pending_edit is None
    assert readonly_event.exited is False
    assert app._error == "'ID' is read-only"

    app._cursor = 1
    _dispatch(app, Keys.Delete)
    assert required.value == "old"
    assert app._error == "'Name' is required — cannot clear"

    app._cursor = 2
    _dispatch(app, Keys.Delete)
    assert optional.value is None
    assert app._error is None

    enter_event = _dispatch(app, Keys.ControlM)
    assert app._pending_edit is optional
    assert enter_event.exited is True


def test_field_editor_edit_field_prompt_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FieldEditorApp([Section("A", [])])
    bool_fd = FieldDescriptor("flag", "Flag", FieldKind.BOOL, False)
    choice_fd = FieldDescriptor(
        "mode", "Mode", FieldKind.CHOICE, "batch", choices=["batch", "queue"]
    )
    list_fd = FieldDescriptor("items", "Items", FieldKind.LIST_STR, ["a"])
    dict_fd = FieldDescriptor("env", "Env", FieldKind.DICT_STR_STR, {"A": "1"})
    int_fd = FieldDescriptor("count", "Count", FieldKind.INT, 1)

    monkeypatch.setattr(questionary, "confirm", lambda *_, **__: _Prompt(True))
    app._edit_field(bool_fd)
    assert bool_fd.value is True

    monkeypatch.setattr(questionary, "select", lambda *_, **__: _Prompt("queue"))
    app._edit_field(choice_fd)
    assert choice_fd.value == "queue"

    monkeypatch.setattr(questionary, "text", lambda *_, **__: _Prompt("a, b, , c"))
    app._edit_field(list_fd)
    assert list_fd.value == ["a", "b", "c"]

    monkeypatch.setattr(questionary, "text", lambda *_, **__: _Prompt("A=2, B=3"))
    app._edit_field(dict_fd)
    assert dict_fd.value == {"A": "2", "B": "3"}

    monkeypatch.setattr(questionary, "text", lambda *_, **__: _Prompt("12"))
    app._edit_field(int_fd)
    assert int_fd.value == 12


def test_field_editor_invalid_edit_keeps_value_and_sets_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FieldEditorApp([Section("A", [])])
    fd = FieldDescriptor("count", "Count", FieldKind.INT, 3)

    monkeypatch.setattr(questionary, "text", lambda *_, **__: _Prompt("bad"))
    app._edit_field(fd)

    assert fd.value == 3
    assert app._error is not None
    assert "Invalid value" in app._error


def test_field_editor_rendering_scrolls_cursor_into_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fields = [
        FieldDescriptor(f"field{i}", f"Field {i}", FieldKind.STRING, str(i)) for i in range(20)
    ]
    app = FieldEditorApp([Section("Many", fields)], title="Scroll")
    app._cursor = 19
    monkeypatch.setattr(
        "gofer.cli.tui_editor.shutil.get_terminal_size",
        lambda fallback: os.terminal_size((80, 8)),
    )

    rendered = app._get_formatted_text()

    assert app._scroll_offset > 0
    assert "Field 19" in str(rendered)
    row = app._render_field_row(fields[19], is_cursor=True)
    assert "Field 19" in row.plain
