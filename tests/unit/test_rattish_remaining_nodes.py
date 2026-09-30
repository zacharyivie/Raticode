from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest

from gofer.core.approvals import ApprovalStore
from gofer.core.resources import ResourceLimits
from gofer.rattish import runtime
from gofer.rattish.compiler import CompileContext, RattishCompiler
from gofer.rattish.diagnostics import RattishCompileError
from gofer.rattish.preflight import run_preflight
from gofer.rattish.provider_contracts import load_provider_contracts
from gofer.rattish.runtime import execute_node
from gofer.rattish.workflow_runtime import execute_workflow

PROJECT_ROOT = Path(__file__).parents[2]
RATTISH_ROOT = PROJECT_ROOT / "rattish"


def compile_source(source: str, project_root: Path) -> dict[str, Any]:
    compiler = RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=sorted((RATTISH_ROOT / "contracts").glob("*.json")),
    )
    providers = load_provider_contracts(
        RATTISH_ROOT / "schemas" / "provider-contract.schema.json",
        sorted((RATTISH_ROOT / "providers").glob("*.json")),
    )
    return compiler.compile(
        source,
        CompileContext("remaining-nodes", project_root, provider_contracts=providers),
    ).ir


@pytest.mark.anyio
async def test_common_llm_task_uses_provider_and_structured_output(
    tmp_path: Path, fake_subscription: Any
) -> None:
    source = (
        """Rattish: 1
Workflow:
  name: Common task
Node summarize:
  type: common-llm-task
  provider: codex
  task: summarize
  target: release notes
  output-schema: """
        '{"type":"object","properties":{"summary":{"type":"string"}},'
        '"required":["summary"],"additionalProperties":false}\n'
    )
    fake_subscription._output = '{"summary":"ready"}'

    result = await execute_node(
        compile_source(source, tmp_path),
        "summarize",
        subscriptions={"codex": fake_subscription},
        data_dir=tmp_path / "data",
    )

    assert result.outcome == "success"
    assert result.output == {"summary": "ready"}
    assert "Summarize" in str(fake_subscription.calls[0]["prompt"])


@pytest.mark.anyio
async def test_local_vectorize_and_search_round_trip(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.txt").write_text(
        "Rattish workflows compile to JSON IR.", encoding="utf-8"
    )
    source = """Rattish: 1
Workflow:
  name: Retrieval
Node index:
  type: local-vectorize
  source-path: docs
  index-path: cache/index.json
  to: search
Node search:
  type: local-search
  index-path: cache/index.json
  query: Rattish workflow
  needs:
    - index
"""

    result = await execute_workflow(compile_source(source, tmp_path))

    assert result.outcome == "pass", result
    assert result.latest_node_outputs["index"]["chunk_count"] == 1
    assert result.latest_node_outputs["search"]["count"] == 1
    assert result.latest_node_outputs["search"]["results"][0]["path"].endswith("guide.txt")


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["copy-file", "move-file"])
@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("destination", ["parent", "parent/alias/.."])
async def test_transfer_cannot_overwrite_a_directory_containing_its_source(
    tmp_path: Path, operation: str, directory: bool, destination: str
) -> None:
    source = tmp_path / "parent" / "source"
    source.parent.mkdir()
    (source.parent / "alias").mkdir()
    if directory:
        source.mkdir()
        content_path = source / "contents.txt"
    else:
        content_path = source
    content_path.write_text("keep source", encoding="utf-8")
    sibling = source.parent / "sibling.txt"
    sibling.write_text("keep sibling", encoding="utf-8")
    ir = compile_source(
        f"Rattish: 1\nWorkflow:\n  name: Safe transfer\nNode transfer:\n"
        f"  type: {operation}\n  source-path: parent/source\n"
        f"  destination-path: {destination}\n  overwrite: true\n",
        tmp_path,
    )

    result = await execute_node(ir, "transfer")

    assert result.outcome == "failure"
    assert content_path.read_text(encoding="utf-8") == "keep source"
    assert sibling.read_text(encoding="utf-8") == "keep sibling"


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["write-file", "prompt-file"])
async def test_non_overwriting_output_preserves_a_concurrently_created_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    target = tmp_path / "output.txt"
    configuration = (
        "  path: output.txt\n  content: new content\n"
        if operation == "write-file"
        else "  output-path: output.txt\n  template: new content\n"
    )
    ir = compile_source(
        f"Rattish: 1\nWorkflow:\n  name: Exclusive output\nNode save:\n"
        f"  type: {operation}\n{configuration}  overwrite: false\n",
        tmp_path,
    )
    original_fsync = runtime.os.fsync

    def competing_writer(descriptor: int) -> None:
        original_fsync(descriptor)
        target.write_text("other writer", encoding="utf-8")

    monkeypatch.setattr(runtime.os, "fsync", competing_writer)
    result = await execute_node(ir, "save")

    assert result.outcome == "failure"
    assert target.read_text(encoding="utf-8") == "other writer"
    assert not list(tmp_path.glob(".output.txt.*.tmp"))


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="Requires POSIX named pipes")
@pytest.mark.parametrize("replacement", ["fifo", "swapped-fifo", "swapped-symlink"])
def test_append_rejects_named_pipes_and_replaced_links_without_blocking(
    tmp_path: Path, replacement: str
) -> None:
    script = """
import asyncio
import os
import sys
from pathlib import Path
from gofer.rattish import runtime
from gofer.rattish.compiler import CompileContext, RattishCompiler

project = Path(sys.argv[1])
contracts = Path(sys.argv[2])
target = project / "output.txt"
protected = project / "private.txt"
protected.write_text("keep private")
replacement = sys.argv[3]
if replacement == "fifo":
    os.mkfifo(target)
else:
    target.write_text("initial")
    original_open = os.open
    def swap_before_open(name, flags, *args, **kwargs):
        if str(name) == str(target):
            target.unlink()
            if replacement == "swapped-fifo":
                os.mkfifo(target)
            else:
                target.symlink_to(protected)
        return original_open(name, flags, *args, **kwargs)
    os.open = swap_before_open
compiler = RattishCompiler.from_paths(
    schema_root=contracts / "schemas",
    contract_paths=[contracts / "contracts/write-file.json"],
)
ir = compiler.compile(
    "Rattish: 1\\nWorkflow:\\n  name: Safe append\\nNode save:\\n"
    "  type: write-file\\n  path: output.txt\\n  content: appended\\n  append: true\\n",
    CompileContext("append", project),
).ir
result = asyncio.run(runtime.execute_node(ir, "save"))
assert result.outcome == "failure", result
assert protected.read_text() == "keep private"
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), str(RATTISH_ROOT), replacement],
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.anyio
async def test_local_vectorize_stops_scanning_at_limit(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    entries = [docs / str(index) for index in range(4)]
    for entry in entries:
        entry.write_text("data")
    ir = compile_source(
        "Rattish: 1\nWorkflow:\n  name: Bounded index\nNode index:\n"
        "  type: local-vectorize\n  source-path: docs\n  index-path: index.json\n",
        tmp_path,
    )
    seen = []

    def bounded_scan(self, pattern):
        for entry in entries:
            seen.append(entry)
            yield entry

    monkeypatch.setattr(Path, "rglob", bounded_scan)
    monkeypatch.setattr(Path, "glob", bounded_scan)
    monkeypatch.setattr(runtime, "DEFAULT_RESOURCE_LIMITS", ResourceLimits(max_files_scanned=2))
    result = await execute_node(ir, "index")
    assert result.outcome == "failure"
    assert len(seen) == 3
    assert not (tmp_path / "index.json").exists()


@pytest.mark.anyio
async def test_local_vectorize_bounds_existing_index_read(tmp_path, monkeypatch):
    (tmp_path / "source.txt").write_text("data")
    (tmp_path / "index.json").write_text(" " * 9)
    ir = compile_source(
        "Rattish: 1\nWorkflow:\n  name: Bounded index\nNode index:\n"
        "  type: local-vectorize\n  source-path: source.txt\n  index-path: index.json\n",
        tmp_path,
    )
    monkeypatch.setattr(
        runtime, "DEFAULT_RESOURCE_LIMITS", ResourceLimits(max_vector_index_bytes=8)
    )
    result = await execute_node(ir, "index")
    assert result.outcome == "failure"
    assert result.error is not None
    assert "limit 8 bytes" in result.error.message
    assert (tmp_path / "index.json").read_text() == " " * 9


@pytest.mark.anyio
async def test_approval_gate_waits_for_external_decision(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Approval
Node approve:
  type: approval-gate
  message: Ship it?
"""
    ir = compile_source(source, tmp_path)
    store = ApprovalStore(tmp_path / "data")
    result_holder: dict[str, Any] = {}

    async def run_gate() -> None:
        result_holder["result"] = await execute_node(
            ir,
            "approve",
            data_dir=tmp_path / "data",
            approval_store=store,
            run_id="test-run",
        )

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_gate)
        while not store.list_pending():
            await anyio.sleep(0.01)
        store.decide("remaining-nodes", "test-run", "approve", "approved", decided_by="owner")

    result = result_holder["result"]
    assert result.outcome == "success"
    assert result.output["decision"] == "approved"


@pytest.mark.anyio
async def test_approval_gate_renders_with_local_from_agent_string_output(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Approval with local
Node writer:
  type: agent
  provider: codex
  prompt: Say cat
  to: approve
Node approve:
  type: approval-gate
  with:
    cat: node.writer.output
  message: What should I do? {{cat}}
  needs: writer
"""
    ir = compile_source(source, tmp_path)
    store = ApprovalStore(tmp_path / "data")
    result_holder: dict[str, Any] = {}

    async def run_gate() -> None:
        result_holder["result"] = await execute_node(
            ir,
            "approve",
            node_outputs={"writer": "cat"},
            data_dir=tmp_path / "data",
            approval_store=store,
            run_id="local-run",
        )

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_gate)
        while not store.list_pending():
            await anyio.sleep(0.01)
        pending = store.list_pending()[0]
        assert pending.message == "What should I do? cat"
        store.decide(
            "remaining-nodes",
            "local-run",
            "approve",
            "approved",
            decided_by="owner",
        )

    result = result_holder["result"]
    assert result.outcome == "success"
    assert result.output["message"] == "What should I do? cat"


@pytest.mark.anyio
async def test_count_loop_routes_one_activation_per_item(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Count loop
Node repeat:
  type: loop
  source: {"type": "count", "count": 3, "max-concurrency": 2}
  to: capture
Node capture:
  type: bash-command
  command: printf '%s' "$ITEM"
  needs:
    - repeat
  with:
    item: node.repeat.output.index
"""

    result = await execute_workflow(compile_source(source, tmp_path))

    assert result.outcome == "pass", result
    assert [run.node_id for run in result.runs].count("capture") == 3


@pytest.mark.anyio
async def test_break_closes_infinite_loop_lineage(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Break loop
  max-runs: 10
Node repeat:
  type: loop
  source: {"type": "infinite"}
  to: stop
Node stop:
  type: break
  loop: repeat
  needs:
    - repeat
"""

    result = await execute_workflow(compile_source(source, tmp_path))

    assert result.outcome == "pass"
    assert [run.node_id for run in result.runs] == ["repeat", "stop"]
    assert result.runs[-1].result.output["loop"] == "repeat"


def test_loop_defaults_are_frozen_in_ir(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Loop defaults
Node repeat:
  type: loop
  source: {"type": "count"}
"""

    ir = compile_source(source, tmp_path)

    assert ir["nodes"][0]["configuration"]["source"] == {
        "type": "count",
        "count": 1,
        "max_concurrency": 1,
        "fail_fast": False,
    }


def test_loop_directory_source_requires_path_and_allows_parent_paths(tmp_path: Path) -> None:
    missing = """Rattish: 1
Workflow:
  name: Missing loop path
Node repeat:
  type: loop
  source: {"type": "directory"}
"""
    escaping = missing.replace(
        '{"type": "directory"}', '{"type": "directory", "path": "../outside"}'
    )

    with pytest.raises(RattishCompileError) as missing_error:
        compile_source(missing, tmp_path)
    assert (
        compile_source(escaping, tmp_path)["nodes"][0]["configuration"]["source"]["path"]
        == "../outside"
    )

    assert "RATTISH_MISSING_FIELD" in {item.code for item in missing_error.value.diagnostics}


@pytest.mark.anyio
async def test_loop_non_fail_fast_finishes_items_then_fails_workflow(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Settled loop failure
Node repeat:
  type: loop
  source: {"type": "count", "count": 3, "max-concurrency": 1}
  to: check
Node check:
  type: bash-command
  command: test "$ITEM" != "1"
  needs:
    - repeat
  with:
    item: node.repeat.output.index
"""

    result = await execute_workflow(compile_source(source, tmp_path))

    assert result.outcome == "failure"
    assert [run.node_id for run in result.runs].count("check") == 3


def test_remaining_node_preflight_checks_resources(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Missing retrieval resources
Node index:
  type: local-vectorize
  source-path: missing-docs
  index-path: cache/index.json
Node search:
  type: local-search
  index-path: missing-index.json
  query: rattish
"""

    result = run_preflight(compile_source(source, tmp_path), data_dir=tmp_path / "data")

    assert not result.ready
    assert {item.code for item in result.diagnostics} == {"RATTISH_PREFLIGHT_RESOURCE_MISSING"}


@pytest.mark.anyio
async def test_trigger_event_loop_uses_runtime_trigger_channel(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Trigger events
Node repeat:
  type: loop
  source: {"type": "trigger-events"}
  to: capture
Node capture:
  type: bash-command
  command: printf '%s' "$EVENT"
  needs:
    - repeat
  with:
    event: node.repeat.output.kind
"""

    result = await execute_workflow(
        compile_source(source, tmp_path),
        trigger_events=[{"kind": "created"}, {"kind": "modified"}],
    )

    captures = [run for run in result.runs if run.node_id == "capture"]
    assert result.outcome == "pass"
    assert [run.result.output["stdout"] for run in captures] == ["created", "modified"]


@pytest.mark.anyio
@pytest.mark.parametrize("include_content", [False, True])
async def test_trigger_event_loop_enforces_item_and_content_limits(
    tmp_path, monkeypatch, include_content
) -> None:
    (tmp_path / "event.txt").write_text("12345")
    monkeypatch.setattr(
        runtime,
        "DEFAULT_RESOURCE_LIMITS",
        ResourceLimits(max_fanout_items=2, max_aggregate_read_bytes=8),
    )
    source = f"""Rattish: 1
Workflow:
  name: Bounded trigger events
Node repeat:
  type: loop
  source: {{"type": "trigger-events", "include-content": {str(include_content).lower()}}}
"""
    events = [{"path": "event.txt"}] * (2 if include_content else 3)
    result = await execute_node(compile_source(source, tmp_path), "repeat", trigger_events=events)
    assert result.outcome == "failure"
    assert result.error is not None
    assert ("aggregate" if include_content else "fan-out") in result.error.message


@pytest.mark.anyio
@pytest.mark.parametrize("files", [False, True])
async def test_directory_loop_stops_enumerating_at_resource_limit(tmp_path, monkeypatch, files):
    seen = []
    entries = []
    for index in range(4):
        entry = tmp_path / str(index)
        if files:
            entry.write_text("data")
        else:
            entry.mkdir()
        entries.append(entry)

    def bounded_glob(self, pattern):
        for entry in entries:
            seen.append(entry)
            yield entry

    monkeypatch.setattr(
        runtime, "DEFAULT_RESOURCE_LIMITS", ResourceLimits(max_fanout_items=2, max_files_scanned=2)
    )
    compiler = RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas", contract_paths=[RATTISH_ROOT / "contracts/loop.json"]
    )
    ir = compiler.compile(
        "Rattish: 1\nWorkflow:\n  name: Bounded directory\nNode repeat:\n"
        '  type: loop\n  source: {"type": "directory", "path": "."}\n',
        CompileContext("bounded-directory", tmp_path),
    ).ir
    monkeypatch.setattr(Path, "glob", bounded_glob)
    result = await execute_node(ir, "repeat")
    assert result.outcome == "failure"
    assert len(seen) == 3


@pytest.mark.parametrize("suffix", [".csv", ".jsonl"])
def test_tabular_loop_stops_parsing_at_item_limit(tmp_path, monkeypatch, suffix):
    path = tmp_path / ("rows" + suffix)
    path.write_text(
        "name\nfirst\nsecond\nthird\nfourth\n" if suffix == ".csv" else "{}\n{}\n{}\n{}\n"
    )
    monkeypatch.setattr(runtime, "DEFAULT_RESOURCE_LIMITS", ResourceLimits(max_fanout_items=2))
    context = runtime.RuntimeContext(tmp_path, "test", "test", {}, (), {})
    seen = []
    if suffix == ".csv":
        original_reader = runtime.csv.DictReader

        def observed_reader(*args, **kwargs):
            for row in original_reader(*args, **kwargs):
                seen.append(row)
                yield row

        monkeypatch.setattr(runtime.csv, "DictReader", observed_reader)
    else:
        original_loads = runtime.json.loads

        def observed_loads(*args, **kwargs):
            seen.append(args[0])
            return original_loads(*args, **kwargs)

        monkeypatch.setattr(runtime.json, "loads", observed_loads)
    with pytest.raises(ValueError, match="fan-out"):
        runtime._tabular_loop_items(path, context)
    assert len(seen) <= 3 if suffix == ".csv" else len(seen) <= 2


@pytest.mark.anyio
@pytest.mark.parametrize("source_type", ["directory", "trigger-events"])
async def test_loop_accounts_for_actual_content_bytes(tmp_path, monkeypatch, source_type):
    import os

    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_bytes(b"12345")
    loop_source = {"type": source_type, "include-content": True}
    if source_type == "directory":
        loop_source["path"] = "."
    ir = compile_source(
        "Rattish: 1\nWorkflow:\n  name: Actual read budget\nNode repeat:\n  type: loop\n"
        f"  source: {json.dumps(loop_source)}\n",
        tmp_path,
    )
    original_stat = Path.stat

    def stale_size(self, *args, **kwargs):
        metadata = original_stat(self, *args, **kwargs)
        if self.parent == tmp_path and self.suffix == ".txt":
            values = list(metadata)
            values[6] = 1
            return os.stat_result(values)
        return metadata

    monkeypatch.setattr(Path, "stat", stale_size)
    monkeypatch.setattr(
        runtime, "DEFAULT_RESOURCE_LIMITS", ResourceLimits(max_aggregate_read_bytes=8)
    )
    result = await execute_node(ir, "repeat", trigger_events=[{"path": "a.txt"}, {"path": "b.txt"}])
    assert result.outcome == "failure"
    assert result.error is not None
    assert "limit" in result.error.message


@pytest.mark.parametrize("suffix", [".csv", ".jsonl"])
def test_tabular_loop_accepts_exact_item_limit_and_preserves_newlines(
    tmp_path, monkeypatch, suffix
):
    path = tmp_path / ("rows" + suffix)
    path.write_text('name\n"first\r\nline"\nsecond\n' if suffix == ".csv" else "{}\n{}\n")
    monkeypatch.setattr(runtime, "DEFAULT_RESOURCE_LIMITS", ResourceLimits(max_fanout_items=2))
    context = runtime.RuntimeContext(tmp_path, "test", "test", {}, (), {})
    items = runtime._tabular_loop_items(path, context)
    assert items == (
        [{"name": "first\r\nline"}, {"name": "second"}] if suffix == ".csv" else [{}, {}]
    )


@pytest.mark.parametrize("row_count", [0, 2, 3])
def test_xlsx_loop_bounds_rows_and_closes_workbook(tmp_path, monkeypatch, row_count):
    from datetime import datetime

    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "rows.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["name", "when"])
    for index in range(row_count):
        sheet.append([str(index), datetime(2026, 1, 1)])
    workbook.save(path)
    workbook.close()
    original_load = openpyxl.load_workbook
    closed = []

    def load_workbook(*args, **kwargs):
        loaded = original_load(*args, **kwargs)
        close = loaded.close

        def record_close():
            closed.append(True)
            close()

        monkeypatch.setattr(loaded, "close", record_close)
        return loaded

    monkeypatch.setattr(openpyxl, "load_workbook", load_workbook)
    monkeypatch.setattr(runtime, "DEFAULT_RESOURCE_LIMITS", ResourceLimits(max_fanout_items=2))
    context = runtime.RuntimeContext(tmp_path, "test", "test", {}, (), {})
    if row_count > 2:
        with pytest.raises(ValueError, match="fan-out"):
            runtime._tabular_loop_items(path, context)
    else:
        items = runtime._tabular_loop_items(path, context)
        assert items == [
            {"name": str(index), "when": datetime(2026, 1, 1)} for index in range(row_count)
        ]
    assert closed == [True]


@pytest.mark.anyio
async def test_loop_join_state_is_isolated_per_iteration(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Isolated loop joins
Node repeat:
  type: loop
  source: {"type": "count", "count": 2, "max-concurrency": 2}
  to:
    - left
    - right
Node left:
  type: bash-command
  command: if [ "$ITEM" = 0 ]; then sleep 0.1; fi; printf 'L%s' "$ITEM"
  needs:
    - repeat
  with:
    item: node.repeat.output.index
  to: joined
Node right:
  type: bash-command
  command: if [ "$ITEM" = 1 ]; then sleep 0.1; fi; printf 'R%s' "$ITEM"
  needs:
    - repeat
  with:
    item: node.repeat.output.index
  to: joined
Node joined:
  type: bash-command
  command: printf '%s-%s' "$LEFT" "$RIGHT"
  needs:
    - left
    - right
  with:
    left: node.left.output.stdout
    right: node.right.output.stdout
"""

    result = await execute_workflow(compile_source(source, tmp_path))

    joined = [run.result.output["stdout"] for run in result.runs if run.node_id == "joined"]
    assert result.outcome == "pass"
    assert set(joined) == {"L0-R0", "L1-R1"}


@pytest.mark.anyio
@pytest.mark.parametrize("with_unmatched_file", [False, True])
async def test_empty_directory_loop_succeeds_without_body(
    tmp_path: Path, with_unmatched_file: bool
) -> None:
    (tmp_path / "tickets").mkdir()
    if with_unmatched_file:
        (tmp_path / "tickets" / "ignore.txt").write_text("ignored")
    source = """Rattish: 1
Workflow:
  name: Empty tickets
Node repeat:
  type: loop
  source: {"type": "directory", "path": "tickets", "glob": "*.md"}
  to: body
Node body:
  type: bash-command
  command: exit 99
  needs: repeat
"""
    ir = compile_source(source, tmp_path)
    assert run_preflight(ir).ready
    result = await execute_workflow(ir)
    assert result.outcome == "pass"
    assert [run.node_id for run in result.runs] == ["repeat"]


@pytest.mark.anyio
async def test_loop_source_can_be_created_by_upstream_node(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Generated tickets
Node create:
  type: bash-command
  command: mkdir tickets && printf ticket > tickets/one.md
  to: repeat
Node repeat:
  type: loop
  source: {"type": "directory", "path": "tickets", "glob": "*.md"}
  needs: create
  to: body
Node body:
  type: bash-command
  command: printf done
  needs: repeat
"""
    ir = compile_source(source, tmp_path)
    assert run_preflight(ir).ready
    result = await execute_workflow(ir)
    assert result.outcome == "pass"
    assert [run.node_id for run in result.runs] == ["create", "repeat", "body"]


@pytest.mark.anyio
async def test_loop_missing_directory_fails_at_activation(tmp_path: Path) -> None:
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: Missing tickets
Node repeat:
  type: loop
  source: {"type": "directory", "path": "missing"}
""",
        tmp_path,
    )
    assert run_preflight(ir).ready
    result = await execute_workflow(ir)
    assert result.outcome == "failure"
    assert result.runs[0].result.outcome == "failure"


@pytest.mark.anyio
@pytest.mark.parametrize("path_style", ["absolute", "parent", "symlink", "home"])
async def test_loop_reads_authored_external_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path_style: str
) -> None:
    import json

    project = tmp_path / "project"
    project.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "ticket.md").write_text("outside project")
    (project / "linked").symlink_to(external, target_is_directory=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    authored = {
        "absolute": str(external),
        "parent": "../external",
        "symlink": "linked",
        "home": "~/external",
    }[path_style]
    ir = compile_source(
        f"""Rattish: 1
Workflow:
  name: External input
Node repeat:
  type: loop
  source: {{"type": "directory", "path": {json.dumps(authored)}, "include-content": true}}
""",
        project,
    )
    assert run_preflight(ir).ready
    result = await execute_node(ir, "repeat")
    assert result.outcome == "success"
    assert result.output["items"][0]["file_content"] == "outside project"


@pytest.mark.anyio
async def test_loop_reports_os_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gofer.rattish.runtime as runtime

    (tmp_path / "tickets").mkdir()
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: Denied input
Node repeat:
  type: loop
  source: {"type": "directory", "path": "tickets"}
""",
        tmp_path,
    )

    def denied(path):
        raise PermissionError("OS denied directory access")

    monkeypatch.setattr(runtime.os, "scandir", denied)
    result = await execute_node(ir, "repeat")
    assert result.outcome == "failure"
    assert result.error is not None
    assert "OS denied" in result.error.message
