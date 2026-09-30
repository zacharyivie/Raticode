from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from gofer.core.resources import ResourceLimits
from gofer.core.run_outputs import write_run_node_outputs_payload
from gofer.ui import api


def _write_artifact(writer: str, base: Path, log_path: Path) -> Path:
    limits = ResourceLimits()
    if writer == "core_outputs":
        result = SimpleNamespace(
            log_path=log_path,
            workflow_id="fixture",
            node_outputs={},
            parameters={"message": "complete"},
            usage_summary={},
        )
        write_run_node_outputs_payload(result, limits)
        return log_path.with_suffix(".outputs.json")
    if writer == "ui_outputs":
        api._write_run_node_outputs_payload(
            log_path,
            workflow_id="fixture",
            limits=limits,
            node_outputs={},
            node_outputs_truncated=False,
        )
        return log_path.with_suffix(".outputs.json")
    if writer == "trigger":
        api._write_run_trigger_payload(log_path, {"triggerId": "fixture"})
        return api._run_trigger_path(log_path)
    api.write_run_summary_payload(base, "fixture", log_path)
    return api._run_summary_path(log_path)


WRITERS = ["core_outputs", "ui_outputs", "trigger", "summary"]


@pytest.mark.parametrize("writer", WRITERS)
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "dangling"])
def test_run_artifact_writers_preserve_link_targets(tmp_path: Path, writer: str, kind: str) -> None:
    base = tmp_path / "data"
    log_path = base / "logs" / "fixture" / "run.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("workflow finished", encoding="utf-8")
    destination = _write_artifact(writer, base, log_path)
    expected = json.loads(destination.read_text(encoding="utf-8"))
    destination.unlink()
    outside = tmp_path / "outside"
    if kind != "dangling":
        outside.write_bytes(b"outside sentinel")
    try:
        if kind == "hardlink":
            os.link(outside, destination)
        else:
            destination.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Links unavailable: {exc}")

    _write_artifact(writer, base, log_path)

    assert not destination.is_symlink()
    assert json.loads(destination.read_text(encoding="utf-8")) == expected
    if kind == "dangling":
        assert not outside.exists()
    else:
        assert outside.read_bytes() == b"outside sentinel"


@pytest.mark.skipif(os.open not in os.supports_dir_fd, reason="POSIX directory descriptors")
@pytest.mark.parametrize("writer", WRITERS)
def test_run_artifact_writers_refuse_replaced_parent(tmp_path: Path, writer: str) -> None:
    base = tmp_path / "data"
    log_path = base / "logs" / "fixture" / "run.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("workflow finished", encoding="utf-8")
    outside = tmp_path / "outside"
    log_path.parent.rename(outside)
    log_path.parent.symlink_to(outside, target_is_directory=True)
    before = {entry.name: entry.read_bytes() for entry in outside.iterdir()}

    if writer == "trigger":
        _write_artifact(writer, base, log_path)
    elif writer == "summary":
        with pytest.raises(api.WorkflowLogError, match="Invalid path"):
            _write_artifact(writer, base, log_path)
    else:
        with pytest.raises(OSError):
            _write_artifact(writer, base, log_path)

    assert {entry.name: entry.read_bytes() for entry in outside.iterdir()} == before


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_run_index_writer_ignores_planted_temporary_links(tmp_path: Path, kind: str) -> None:
    destination = tmp_path / "index.json"
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside sentinel")
    predictable = destination.with_name(
        f".{destination.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        if kind == "symlink":
            predictable.symlink_to(outside)
        else:
            os.link(outside, predictable)
    except OSError as exc:
        pytest.skip(f"Links unavailable: {exc}")

    api._write_json_atomic(destination, {"version": 1, "runs": {}})

    assert json.loads(destination.read_text(encoding="utf-8")) == {"version": 1, "runs": {}}
    assert outside.read_bytes() == b"outside sentinel"
    assert predictable.read_bytes() == b"outside sentinel"


@pytest.mark.skipif(os.open not in os.supports_dir_fd, reason="POSIX directory descriptors")
def test_run_index_writer_refuses_replaced_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = tmp_path / "indexes"
    parent.symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        api._write_json_atomic(parent / "index.json", {"version": 1})

    assert not list(outside.iterdir())
