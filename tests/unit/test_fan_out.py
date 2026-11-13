from __future__ import annotations

import json
import sys
import threading
import types
from collections.abc import Callable, Iterator
from pathlib import Path

import anyio
import pytest

from gofer.core.agent import AgentConfig, AgentResult
from gofer.core.executor import WorkflowExecutor, _load_tabular, _resolve_fan_items
from gofer.core.graph import GraphNode
from gofer.core.operations import (
    AgentOperation,
    CountFanSource,
    DirectoryFanSource,
    LoopOperation,
    OperationType,
    TabularFanSource,
    TriggerEventsFanSource,
)
from gofer.core.provider_profiles import ResolvedProviderSettings
from gofer.core.resources import ResourceLimitError, ResourceLimits
from gofer.core.workflow import AgenticWorkflow, WorkflowConfig
from tests.conftest import FakeSubscription, envelope_request

# ── _load_tabular ─────────────────────────────────────────────────────────────


def test_load_tabular_jsonl(tmp_path: Path) -> None:
    f = tmp_path / "data.jsonl"
    f.write_text('{"name": "alice", "age": 30}\n{"name": "bob", "age": 25}\n')
    rows = _load_tabular(f)
    assert rows == [
        {"name": "alice", "age": 30, "_row": '{"name": "alice", "age": 30}'},
        {"name": "bob", "age": 25, "_row": '{"name": "bob", "age": 25}'},
    ]


def test_load_tabular_csv(tmp_path: Path) -> None:
    f = tmp_path / "data.csv"
    f.write_text("name,age\nalice,30\nbob,25\n")
    rows = _load_tabular(f)
    assert rows == [
        {"name": "alice", "age": "30", "_row": '{"name": "alice", "age": "30"}'},
        {"name": "bob", "age": "25", "_row": '{"name": "bob", "age": "25"}'},
    ]


def test_load_tabular_csv_row_payload_uses_converted_row(tmp_path: Path) -> None:
    f = tmp_path / "data.csv"
    f.write_text("name,age\nalice,30\n")

    row = _load_tabular(f)[0]

    assert row["_row"] == '{"name": "alice", "age": "30"}'


def test_load_tabular_unsupported_format(tmp_path: Path) -> None:
    f = tmp_path / "data.tsv"
    f.write_text("name\talice\n")
    with pytest.raises(ValueError, match="Unsupported tabular format"):
        _load_tabular(f)


def test_load_tabular_jsonl_skips_blank_lines(tmp_path: Path) -> None:
    f = tmp_path / "data.jsonl"
    f.write_text('\n{"x": 1}\n   \n{"x": 2}\n\n')
    rows = _load_tabular(f)
    assert len(rows) == 2
    assert [row["x"] for row in rows] == [1, 2]


def test_load_tabular_xlsx_missing_optional_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = tmp_path / "data.xlsx"
    f.write_text("")
    monkeypatch.setitem(sys.modules, "openpyxl", None)

    with pytest.raises(ImportError, match="openpyxl is required"):
        _load_tabular(f)


def test_load_tabular_xlsx_converts_headers_and_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = tmp_path / "data.xlsx"
    f.write_text("")
    closed = False

    class Worksheet:
        def iter_rows(self, values_only: bool = False) -> Iterator[tuple[object, ...]]:
            assert values_only is True
            return iter(
                [
                    ("name", 2, None),
                    ("alice", 30, True),
                    ("bob", None, False),
                ]
            )

    class Workbook:
        active = Worksheet()

        def close(self) -> None:
            nonlocal closed
            closed = True

    def load_workbook(path: Path, read_only: bool, data_only: bool) -> Workbook:
        assert path == f
        assert read_only is True
        assert data_only is True
        return Workbook()

    monkeypatch.setitem(
        sys.modules,
        "openpyxl",
        types.SimpleNamespace(load_workbook=load_workbook),
    )

    rows = _load_tabular(f)

    assert rows == [
        {
            "name": "alice",
            "2": 30,
            "None": True,
            "_row": '{"name": "alice", "2": 30, "None": true}',
        },
        {
            "name": "bob",
            "2": None,
            "None": False,
            "_row": '{"name": "bob", "2": null, "None": false}',
        },
    ]
    assert closed is True


def test_load_tabular_jsonl_stops_at_limit_before_parsing_extra_row(
    tmp_path: Path,
) -> None:
    f = tmp_path / "data.jsonl"
    f.write_text('{"x": 1}\n{"x": 2}\nnot-json\n{"x": 4}\n')

    with pytest.raises(ResourceLimitError, match="limit 2 items"):
        _load_tabular(f, max_items=2)


def test_load_tabular_rejects_file_over_byte_limit(tmp_path: Path) -> None:
    f = tmp_path / "data.csv"
    f.write_text("name\n" + ("x" * 20) + "\n")

    with pytest.raises(ResourceLimitError, match="size exceeded limit 10 bytes"):
        _load_tabular(f, max_file_read_bytes=10)


def test_load_tabular_rejects_aggregate_row_bytes_over_limit(tmp_path: Path) -> None:
    f = tmp_path / "data.jsonl"
    f.write_text('{"name": "alice"}\n{"name": "bob"}\n')

    with pytest.raises(ResourceLimitError, match="aggregate limit 20 bytes"):
        _load_tabular(
            f,
            max_file_read_bytes=1_000,
            max_aggregate_read_bytes=20,
        )


def test_resolve_fan_items_tabular_rejects_too_many_rows_during_load(
    tmp_path: Path,
) -> None:
    from gofer.core.executor import ExecutionContext

    f = tmp_path / "data.csv"
    f.write_text("name\nalice\nbob\ncarol\n")
    source = TabularFanSource(type="tabular", path=f)

    with pytest.raises(ResourceLimitError, match="limit 2 items"):
        _resolve_fan_items(
            source,
            ExecutionContext(),
            ResourceLimits(max_fanout_items=2),
        )


# ── _resolve_fan_items ────────────────────────────────────────────────────────


def test_resolve_fan_items_count() -> None:
    from gofer.core.executor import ExecutionContext

    ctx = ExecutionContext()
    items = _resolve_fan_items(CountFanSource(type="count", count=3), ctx)
    assert items == [{"index": "0"}, {"index": "1"}, {"index": "2"}]


def test_resolve_fan_items_count_defaults_blank_values_to_one() -> None:
    from gofer.core.executor import ExecutionContext

    ctx = ExecutionContext()
    assert _resolve_fan_items(CountFanSource(type="count", count=None), ctx) == [{"index": "0"}]
    assert _resolve_fan_items(CountFanSource(type="count", count=""), ctx) == [{"index": "0"}]


def test_resolve_fan_items_count_reports_bad_dynamic_path() -> None:
    from gofer.core.executor import ExecutionContext

    ctx = ExecutionContext()
    with pytest.raises(ValueError, match="Cannot resolve dynamic_count path"):
        _resolve_fan_items(CountFanSource(type="count", count="missing.data.count"), ctx)


def test_resolve_fan_items_directory(tmp_path: Path) -> None:
    from gofer.core.executor import ExecutionContext

    (tmp_path / "a.py").write_text("pass")
    (tmp_path / "b.py").write_text("pass")
    (tmp_path / "skip.txt").write_text("skip")
    ctx = ExecutionContext()
    source = DirectoryFanSource(type="directory", path=tmp_path, glob="*.py")
    items = _resolve_fan_items(source, ctx)
    assert len(items) == 2
    names = {i["file_name"] for i in items}
    assert names == {"a.py", "b.py"}
    assert {i["file_extension"] for i in items} == {".py"}
    assert {i["directory"] for i in items} == {str(tmp_path)}
    assert "file_content" not in items[0]


def test_resolve_fan_items_directory_include_content(tmp_path: Path) -> None:
    from gofer.core.executor import ExecutionContext

    (tmp_path / "hello.txt").write_text("hello world")
    ctx = ExecutionContext()
    source = DirectoryFanSource(type="directory", path=tmp_path, include_content=True)
    items = _resolve_fan_items(source, ctx)
    assert items[0]["file_content"] == "hello world"


def test_resolve_fan_items_directory_rejects_too_many_items(tmp_path: Path) -> None:
    from gofer.core.executor import ExecutionContext

    for index in range(3):
        (tmp_path / f"{index}.txt").write_text("x")
    source = DirectoryFanSource(type="directory", path=tmp_path, glob="*.txt")

    with pytest.raises(ResourceLimitError, match="limit 2 items"):
        _resolve_fan_items(
            source,
            ExecutionContext(),
            ResourceLimits(max_fanout_items=2),
        )


def test_resolve_fan_items_directory_stops_before_consuming_all_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gofer.core.executor import ExecutionContext

    files = []
    for index in range(5):
        path = tmp_path / f"{index}.txt"
        path.write_text("x")
        files.append(path)

    original_glob = Path.glob

    def bounded_glob(path: Path, pattern: str) -> Iterator[Path]:
        if path != tmp_path or pattern != "*.txt":
            yield from original_glob(path, pattern)
            return
        for index, file_path in enumerate(files):
            if index > 2:
                raise AssertionError("directory fan-out consumed past the limit check")
            yield file_path

    monkeypatch.setattr(Path, "glob", bounded_glob)
    source = DirectoryFanSource(type="directory", path=tmp_path, glob="*.txt")

    with pytest.raises(ResourceLimitError, match="limit 2 items"):
        _resolve_fan_items(
            source,
            ExecutionContext(),
            ResourceLimits(max_fanout_items=2),
        )


def test_resolve_fan_items_directory_limits_scanned_paths(tmp_path: Path) -> None:
    from gofer.core.executor import ExecutionContext

    for index in range(3):
        (tmp_path / f"{index}").mkdir()
    source = DirectoryFanSource(type="directory", path=tmp_path, glob="*")

    with pytest.raises(ResourceLimitError, match="scan exceeded limit 2 paths"):
        _resolve_fan_items(
            source,
            ExecutionContext(),
            ResourceLimits(max_fanout_items=10, max_files_scanned=2),
        )


def test_resolve_fan_items_trigger_content_rejects_large_file(tmp_path: Path) -> None:
    from gofer.core.executor import ExecutionContext

    changed = tmp_path / "changed.txt"
    changed.write_text("abcdef")
    ctx = ExecutionContext(trigger={"events": [{"path": str(changed)}]})
    source = TriggerEventsFanSource(type="trigger_events", include_content=True)

    with pytest.raises(ResourceLimitError, match="limit 3 bytes"):
        _resolve_fan_items(
            source,
            ctx,
            ResourceLimits(max_file_read_bytes=3),
        )


# ── Executor integration ──────────────────────────────────────────────────────


def _make_agent_workflow(tmp_path: Path, sub: FakeSubscription) -> tuple[AgenticWorkflow, str]:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Process: {{file_name}}")
    wf = AgenticWorkflow(WorkflowConfig(id="fw", name="Fan Test"))
    wf.register_agent(
        AgentConfig(
            agent_id="proc",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=prompt,
        )
    )
    return wf, "claude_code"


async def test_tabular_fan_out_spawns_one_agent_per_row(tmp_path: Path) -> None:
    sub = FakeSubscription(output="done")
    wf, sub_name = _make_agent_workflow(tmp_path, sub)

    data = tmp_path / "input.jsonl"
    data.write_text('{"file_name": "r1"}\n{"file_name": "r2"}\n{"file_name": "r3"}\n')

    prompt = tmp_path / "prompt.md"
    wf.add_operation(
        GraphNode(
            node_id="loop",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=TabularFanSource(type="tabular", path=data, max_concurrency=2),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="proc",
                prompt_path=prompt,
                working_dir=tmp_path,
            ),
        )
    )
    wf.then("loop", "agent")
    result = await WorkflowExecutor(
        wf,
        {sub_name: sub},
        log_base_dir=tmp_path / "logs",
    ).run()
    assert result.success
    assert len(sub.calls) == 3


async def test_directory_fan_out_spawns_one_agent_per_file(tmp_path: Path) -> None:
    sub = FakeSubscription(output="done")
    wf, sub_name = _make_agent_workflow(tmp_path, sub)

    files_dir = tmp_path / "files"
    files_dir.mkdir()
    for i in range(4):
        (files_dir / f"file{i}.txt").write_text(f"content {i}")

    prompt = tmp_path / "prompt.md"
    prompt.write_text("Process: {{file_path}}")
    wf.add_operation(
        GraphNode(
            node_id="loop",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=DirectoryFanSource(type="directory", path=files_dir, max_concurrency=2),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="proc",
                prompt_path=prompt,
                working_dir=tmp_path,
            ),
        )
    )
    wf.then("loop", "agent")
    result = await WorkflowExecutor(
        wf,
        {sub_name: sub},
        log_base_dir=tmp_path / "logs",
    ).run()
    assert result.success
    assert len(sub.calls) == 4
    assert all(call["extra_paths"] == [] for call in sub.calls)
    assert str(files_dir / "file0.txt") in str(sub.calls[0]["prompt"])


async def test_fan_out_max_concurrency_respected(tmp_path: Path) -> None:
    concurrency_log: list[int] = []
    active: list[int] = [0]
    completed_prompts: list[str] = []

    class TrackingSubscription(FakeSubscription):
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
            on_thought: Callable[[str], None] | None = None,
            provider_settings: ResolvedProviderSettings | None = None,
        ) -> AgentResult:
            active[0] += 1
            concurrency_log.append(active[0])
            await anyio.sleep(0.05)
            active[0] -= 1
            completed_prompts.append(prompt)
            return await super().execute(
                prompt,
                working_dir,
                tools,
                mcp_servers,
                env,
                timeout,
                cancel_event,
                extra_paths,
                max_output_bytes,
            )

    sub = TrackingSubscription()
    wf, sub_name = _make_agent_workflow(tmp_path, sub)
    wf.config.resource_limits = ResourceLimits(max_fanout_concurrency=3)

    data = tmp_path / "input.jsonl"
    data.write_text("\n".join(json.dumps({"i": k}) for k in range(10)) + "\n")

    prompt = tmp_path / "prompt.md"
    wf.add_operation(
        GraphNode(
            node_id="loop",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=TabularFanSource(type="tabular", path=data, max_concurrency=3),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="proc",
                prompt_path=prompt,
                working_dir=tmp_path,
            ),
        )
    )
    wf.then("loop", "agent")
    result = await WorkflowExecutor(
        wf,
        {sub_name: sub},
        log_base_dir=tmp_path / "logs",
    ).run()

    assert result.success
    assert max(concurrency_log) == 3
    assert len(completed_prompts) == 10
    assert len(result.node_runs["agent"]) == 10


async def test_directory_loop_defaults_to_sequential_execution(tmp_path: Path) -> None:
    concurrency_log: list[int] = []
    active: list[int] = [0]

    class TrackingSubscription(FakeSubscription):
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
            on_thought: Callable[[str], None] | None = None,
            provider_settings: ResolvedProviderSettings | None = None,
        ) -> AgentResult:
            active[0] += 1
            concurrency_log.append(active[0])
            await anyio.sleep(0.02)
            active[0] -= 1
            return await super().execute(
                prompt,
                working_dir,
                tools,
                mcp_servers,
                env,
                timeout,
                cancel_event,
                extra_paths,
                max_output_bytes,
            )

    sub = TrackingSubscription()
    wf, sub_name = _make_agent_workflow(tmp_path, sub)

    files_dir = tmp_path / "files"
    files_dir.mkdir()
    for index in range(4):
        (files_dir / f"file{index}.txt").write_text(f"content {index}")

    prompt = tmp_path / "prompt.md"
    prompt.write_text("Process {{file_path}}")
    wf.add_operation(
        GraphNode(
            node_id="loop",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=DirectoryFanSource(type="directory", path=files_dir),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="proc",
                prompt_path=prompt,
                working_dir=tmp_path,
            ),
        )
    )
    wf.then("loop", "agent")

    result = await WorkflowExecutor(
        wf,
        {sub_name: sub},
        log_base_dir=tmp_path / "logs",
    ).run()

    assert result.success
    assert len(sub.calls) == 4
    assert max(concurrency_log) == 1


async def test_fan_out_failures_aggregate_when_fail_fast_false(tmp_path: Path) -> None:
    class PartiallyFailingSubscription(FakeSubscription):
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
            on_thought: Callable[[str], None] | None = None,
            provider_settings: ResolvedProviderSettings | None = None,
        ) -> AgentResult:
            self.calls.append({"prompt": prompt, "extra_paths": extra_paths or []})
            failed = '"i": 1' in envelope_request(prompt)
            return AgentResult(
                agent_id="",
                success=not failed,
                output="boom" if failed else "ok",
                exit_code=1 if failed else 0,
                duration_seconds=0.0,
            )

    sub = PartiallyFailingSubscription()
    wf, sub_name = _make_agent_workflow(tmp_path, sub)

    data = tmp_path / "input.jsonl"
    data.write_text("\n".join(json.dumps({"i": k}) for k in range(4)) + "\n")
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Process {{i}}")
    wf.add_operation(
        GraphNode(
            node_id="loop",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=TabularFanSource(
                    type="tabular",
                    path=data,
                    fail_fast=False,
                    max_concurrency=2,
                ),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="proc",
                prompt_path=prompt,
                working_dir=tmp_path,
            ),
        )
    )
    wf.then("loop", "agent")

    result = await WorkflowExecutor(
        wf,
        {sub_name: sub},
        log_base_dir=tmp_path / "logs",
    ).run()

    assert not result.success
    assert len(sub.calls) == 4
    assert len(result.node_runs["agent"]) == 4
    assert [run.success for run in result.node_runs["agent"]].count(False) == 1


async def test_fan_out_fail_fast(tmp_path: Path) -> None:
    class FailingSubscription(FakeSubscription):
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
            on_thought: Callable[[str], None] | None = None,
            provider_settings: ResolvedProviderSettings | None = None,
        ) -> AgentResult:
            raise RuntimeError("boom")

    sub = FailingSubscription()
    wf, sub_name = _make_agent_workflow(tmp_path, sub)

    data = tmp_path / "input.jsonl"
    data.write_text('{"x": 1}\n{"x": 2}\n{"x": 3}\n')

    prompt = tmp_path / "prompt.md"
    wf.add_operation(
        GraphNode(
            node_id="loop",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=TabularFanSource(
                    type="tabular", path=data, fail_fast=True, max_concurrency=1
                ),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="proc",
                prompt_path=prompt,
                working_dir=tmp_path,
            ),
        )
    )
    wf.then("loop", "agent")
    result = await WorkflowExecutor(
        wf,
        {sub_name: sub},
        log_base_dir=tmp_path / "logs",
    ).run()
    assert not result.success


async def test_fan_out_fail_fast_cancels_pending_iterations(tmp_path: Path) -> None:
    started: list[str] = []

    class CancelOnFirstSubscription(FakeSubscription):
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
            on_thought: Callable[[str], None] | None = None,
            provider_settings: ResolvedProviderSettings | None = None,
        ) -> AgentResult:
            started.append(prompt)
            if '"i": 0' in envelope_request(prompt):
                return AgentResult(
                    agent_id="",
                    success=False,
                    output="first failed",
                    exit_code=1,
                    duration_seconds=0.0,
                )
            await anyio.sleep(1)
            return AgentResult(
                agent_id="",
                success=True,
                output="late success",
                exit_code=0,
                duration_seconds=0.0,
            )

    sub = CancelOnFirstSubscription()
    wf, sub_name = _make_agent_workflow(tmp_path, sub)
    data = tmp_path / "input.jsonl"
    data.write_text("\n".join(json.dumps({"i": k}) for k in range(6)) + "\n")
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Process {{i}}")
    wf.add_operation(
        GraphNode(
            node_id="loop",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=TabularFanSource(
                    type="tabular",
                    path=data,
                    fail_fast=True,
                    max_concurrency=3,
                ),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="proc",
                prompt_path=prompt,
                working_dir=tmp_path,
            ),
        )
    )
    wf.then("loop", "agent")

    with anyio.fail_after(2):
        result = await WorkflowExecutor(
            wf,
            {sub_name: sub},
            log_base_dir=tmp_path / "logs",
        ).run()

    assert not result.success
    assert 1 <= len(started) < 6


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("name", "NAME"),
        ("build_mode", "BUILD_MODE"),
        ("_row", "_ROW"),
        ("path", None),
        ("bash_env", None),
        ("BASH_FUNC_x%%", None),
        ("env", None),
        ("ld_preload", None),
        ("dyld_insert_libraries", None),
        ("git_ssh_command", None),
        ("git_config_key_0", None),
        ("pythonpath", None),
        ("node_options", None),
        ("perl5opt", None),
        ("ifs", None),
        ("gofer_ui_api_token", None),
        ("build-mode", None),
        ("a=b", None),
        ("", None),
    ],
)
def test_loop_item_fields_never_become_shell_or_loader_variables(
    key: str, expected: str | None
) -> None:
    """Webhook or CSV field names can't set BASH_ENV, LD_PRELOAD, GIT_* and similar."""
    from gofer.core.executor import loop_item_env_name

    assert loop_item_env_name(key) == expected
