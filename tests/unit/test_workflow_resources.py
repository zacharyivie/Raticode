from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from gofer.core.executor import ExecutionContext, _resolve_fan_items
from gofer.core.graph import GraphNode
from gofer.core.operations import (
    DirectoryFanSource,
    LocalVectorizeOperation,
    LoopOperation,
    OperationType,
    TriggerEventsFanSource,
)
from gofer.core.resources import (
    ResourceLimitError,
    ResourceLimits,
    bundle_resource_limits_from_env,
    read_bytes_limited,
    read_text_limited,
    tail_text_file,
)
from gofer.core.workflow import AgenticWorkflow, WatchConfig, WorkflowConfig


@pytest.mark.parametrize("field", ResourceLimits.model_fields)
def test_workflow_rejects_negative_resource_limits(field: str) -> None:
    with pytest.raises(ValidationError):
        WorkflowConfig(id="limits", name="Limits", resource_limits={field: -1})


@pytest.mark.parametrize("field", ["max_watcher_concurrency", "max_fanout_concurrency"])
def test_resource_concurrency_requires_at_least_one_worker(field: str) -> None:
    with pytest.raises(ValidationError):
        ResourceLimits.model_validate({field: 0})


@pytest.mark.parametrize("ratio", [0, float("nan"), float("inf"), float("-inf")])
def test_bundle_compression_ratio_requires_a_finite_positive_limit(ratio: float) -> None:
    with pytest.raises(ValidationError):
        ResourceLimits(max_bundle_compression_ratio=ratio)


@pytest.mark.parametrize(
    "variable,value",
    [
        ("GOFER_BUNDLE_MAX_ENTRY_BYTES", "-2"),
        ("GOFER_BUNDLE_MAX_COMPRESSION_RATIO", "nan"),
        ("GOFER_BUNDLE_MAX_COMPRESSION_RATIO", "inf"),
        ("GOFER_BUNDLE_MAX_COMPRESSION_RATIO", "0"),
    ],
)
def test_bundle_environment_overrides_are_validated(monkeypatch, variable: str, value: str) -> None:
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValidationError):
        bundle_resource_limits_from_env()


def test_bundle_environment_overrides_preserve_other_limits(monkeypatch) -> None:
    monkeypatch.setenv("GOFER_BUNDLE_MAX_ENTRY_BYTES", "512")
    base = ResourceLimits(max_file_read_bytes=123, max_bundle_compression_ratio=7)
    limits = bundle_resource_limits_from_env(base)
    assert limits.max_bundle_entry_bytes == 512
    assert limits.max_file_read_bytes == 123
    assert limits.max_bundle_compression_ratio == 7
    assert base.max_bundle_entry_bytes == 10_000_000


def test_zero_resource_budgets_remain_available() -> None:
    fields = set(ResourceLimits.model_fields) - {
        "max_bundle_compression_ratio",
        "max_watcher_concurrency",
        "max_fanout_concurrency",
    }
    limits = ResourceLimits.model_validate(dict.fromkeys(fields, 0))
    assert all(getattr(limits, field) == 0 for field in fields)


def test_file_read_limit_catches_growth_after_stat(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "growing.txt"
    path.write_bytes(b"small")
    original_stat = Path.stat
    original_open = open
    read_sizes = []

    def grow_after_stat(self, *args, **kwargs):
        result = original_stat(self, *args, **kwargs)
        if self == path:
            path.write_bytes(b"x" * 100)
        return result

    monkeypatch.setattr(Path, "stat", grow_after_stat)

    def observe_open(self, *args, **kwargs):
        source = original_open(self, *args, **kwargs)
        if self == path and args == ("rb",):
            read = source.read

            def observed_read(size=-1):
                read_sizes.append(size)
                return read(size)

            monkeypatch.setattr(source, "read", observed_read)
        return source

    monkeypatch.setattr("builtins.open", observe_open)
    with pytest.raises(ResourceLimitError, match="exceeded limit 8 bytes"):
        read_text_limited(path, max_bytes=8)
    assert read_sizes == [9]


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_bounded_text_read_preserves_decoding_and_newlines(tmp_path: Path, encoding: str) -> None:
    path = tmp_path / "text.txt"
    content = "café\r\nsecond\rthird\n"
    data = content.encode(encoding)
    path.write_bytes(data)
    result = read_text_limited(path, encoding=encoding, max_bytes=len(data))
    assert result == "café\nsecond\nthird\n"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="Requires POSIX named pipes")
@pytest.mark.parametrize("reader", ["read_bytes_limited", "tail_text_file", "read_text_file_range"])
@pytest.mark.parametrize("replace_file", [False, True])
def test_bounded_file_read_rejects_named_pipes_without_blocking(
    tmp_path: Path, reader: str, replace_file: bool
) -> None:
    path = tmp_path / "input"
    if replace_file:
        path.write_bytes(b"ordinary file")
    else:
        os.mkfifo(path)
    script = """
import os
import sys
from pathlib import Path
from gofer.core import resources

path = Path(sys.argv[1])
if sys.argv[3] == "True":
    original_open = os.open
    def swap_before_open(name, flags, *args, **kwargs):
        if str(name) == str(path):
            path.unlink()
            os.mkfifo(path)
        return original_open(name, flags, *args, **kwargs)
    os.open = swap_before_open
try:
    getattr(resources, sys.argv[2])(path, max_bytes=32)
except OSError as exc:
    assert "not an ordinary file" in str(exc), str(exc)
else:
    raise AssertionError("A named pipe must not be read as a file")
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), reader, str(replace_file)],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")},
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr


def test_bounded_file_read_retains_regular_file_symlink_support(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_bytes(b"content")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symlinks are unavailable")
    assert read_bytes_limited(link, max_bytes=7) == b"content"


@pytest.mark.parametrize("source_type", ["directory", "trigger_events"])
@pytest.mark.parametrize("file_limit,aggregate_limit", [(8, 100), (100, 8)])
def test_loop_content_read_bounds_growth_after_stat(
    tmp_path, monkeypatch, source_type, file_limit, aggregate_limit
) -> None:
    path = tmp_path / "growing.txt"
    path.write_bytes(b"small")
    original_stat = Path.stat
    original_open = open
    read_sizes = []

    def grow_after_stat(self, *args, **kwargs):
        result = original_stat(self, *args, **kwargs)
        if self == path:
            path.write_bytes(b"x" * 100)
            metadata = list(result)
            metadata[6] = 5
            return os.stat_result(metadata)
        return result

    def observe_open(self, *args, **kwargs):
        stream = original_open(self, *args, **kwargs)
        if self == path and args == ("rb",):
            read = stream.read

            def bounded_read(size=-1):
                read_sizes.append(size)
                return read(size)

            monkeypatch.setattr(stream, "read", bounded_read)
        return stream

    monkeypatch.setattr(Path, "stat", grow_after_stat)
    monkeypatch.setattr("builtins.open", observe_open)
    source = (
        DirectoryFanSource(type="directory", path=tmp_path, glob="*.txt", include_content=True)
        if source_type == "directory"
        else TriggerEventsFanSource(type="trigger_events", include_content=True)
    )
    with pytest.raises(ResourceLimitError):
        _resolve_fan_items(
            source,
            ExecutionContext(trigger={"events": [{"path": str(path)}]}),
            ResourceLimits(
                max_file_read_bytes=file_limit, max_aggregate_read_bytes=aggregate_limit
            ),
        )
    assert read_sizes == [min(file_limit, aggregate_limit) + 1]


def test_log_tail_bounds_reads_when_file_grows_after_stat(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "growing.log"
    path.write_bytes(b"small")
    original_stat = Path.stat
    original_open = open
    read_sizes = []

    def grow_after_stat(self, *args, **kwargs):
        result = original_stat(self, *args, **kwargs)
        if self == path:
            path.write_bytes(b"x" * 100)
        return result

    def observe_open(self, *args, **kwargs):
        source = original_open(self, *args, **kwargs)
        if self == path and args == ("rb",):
            read = source.read

            def observed_read(size=-1):
                read_sizes.append(size)
                return read(size)

            monkeypatch.setattr(source, "read", observed_read)
        return source

    monkeypatch.setattr(Path, "stat", grow_after_stat)
    monkeypatch.setattr("builtins.open", observe_open)
    text, truncated = tail_text_file(path, 8)
    assert text == "x" * 8
    assert truncated
    assert read_sizes == [9]


@pytest.mark.parametrize(
    ("data", "limit", "expected", "truncated"),
    [
        (b"small", 8, "small", False),
        (b"12345678", 8, "12345678", False),
        (b"1234567890", 8, "34567890", True),
        (b"text", 0, "", True),
        (b"", 0, "", False),
    ],
)
def test_log_tail_preserves_limit_and_truncation(
    tmp_path: Path, data: bytes, limit: int, expected: str, truncated: bool
) -> None:
    path = tmp_path / "run.log"
    path.write_bytes(data)
    assert tail_text_file(path, limit) == (expected, truncated)


def test_validate_surfaces_resource_risk_warnings(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    for index in range(3):
        (docs / f"{index}.txt").write_text("x")
    workflow = AgenticWorkflow(
        WorkflowConfig(
            id="resource-warnings",
            name="Resource Warnings",
            watch=WatchConfig(path=docs, max_concurrency=8),
            resource_limits=ResourceLimits(max_fanout_items=2, max_files_scanned=2),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="fanout",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=DirectoryFanSource(
                    type="directory",
                    path=docs,
                    glob="*.txt",
                    include_content=True,
                ),
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="index",
            operation=LocalVectorizeOperation(
                type=OperationType.LOCAL_VECTORIZE,
                source_path=docs,
                index_path=tmp_path / "index.json",
                glob="*.txt",
            ),
        )
    )

    with pytest.warns(UserWarning) as warnings:
        workflow.validate()

    messages = "\n".join(str(warning.message) for warning in warnings)
    assert "directory fan-out includes file content" in messages
    assert "may exceed max_fanout_items=2" in messages
    assert "local_vectorize scans local files" in messages
    assert "may exceed max_files_scanned=2" in messages
    assert "oldest queued event batches are dropped on overflow" in messages
    assert "will be capped by global max_watcher_concurrency=2" in messages
