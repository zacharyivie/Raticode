from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gofer.core import provider_capabilities
from gofer.core.runner import (
    RunnerQueueStore,
    capabilities_match,
    default_runner_capabilities,
    run_worker_once,
    workflow_required_capabilities,
)
from gofer.core.workflow import AgenticWorkflow


def _write_pass_workflow(path: Path, workflow_id: str = "queued") -> None:
    path.write_text(
        f"""
[workflow]
id = "{workflow_id}"
name = "Queued"

[[nodes]]
id = "start"
type = "pass"
message = "ok"
""",
        encoding="utf-8",
    )


def test_runner_queue_persists_runs_and_runners(tmp_path: Path) -> None:
    workflow_path = tmp_path / "queued.toml"
    _write_pass_workflow(workflow_path)
    workflow = AgenticWorkflow.from_file(workflow_path)
    store = RunnerQueueStore(tmp_path)

    runner = store.register_runner(
        "runner-1",
        "CI worker",
        ["linux", "ci"],
        {"provider_clis": ["codex"], "workspace_roots": [str(tmp_path)]},
    )
    queued = store.enqueue(
        workflow.config.id,
        workflow_path,
        priority=7,
        trigger="manual",
        target_labels=["ci"],
        required_capabilities=workflow_required_capabilities(workflow),
    )

    reloaded = RunnerQueueStore(tmp_path)

    assert reloaded.get_runner(runner.id) == runner
    assert reloaded.get_run(queued.id) == queued


def test_runner_capability_matching_reports_mismatch() -> None:
    matches, message = capabilities_match(
        ["linux"],
        {"provider_clis": ["codex"]},
        ["prod"],
        {"provider_clis": ["claude_code"]},
    )

    assert matches is False
    assert message == "Runner missing label(s): prod"


def test_runner_claim_skips_mismatched_runs(tmp_path: Path) -> None:
    workflow_path = tmp_path / "queued.toml"
    _write_pass_workflow(workflow_path)
    store = RunnerQueueStore(tmp_path)
    store.register_runner("runner-1", "local", ["linux"], {"provider_clis": []})
    queued = store.enqueue(
        "queued",
        workflow_path,
        target_labels=["gpu"],
        required_capabilities={"provider_clis": []},
    )

    claimed = store.claim_next("runner-1")

    assert claimed is None
    refreshed = store.get_run(queued.id)
    assert refreshed is not None
    assert refreshed.status == "queued"
    assert refreshed.message == "Runner missing label(s): gpu"


def test_runner_executes_queued_workflow(tmp_path: Path) -> None:
    workflow_path = tmp_path / "queued.toml"
    _write_pass_workflow(workflow_path)
    store = RunnerQueueStore(tmp_path)
    store.register_runner("runner-1", "local", [], {"provider_clis": []})
    queued = store.enqueue("queued", workflow_path)

    result = run_worker_once(store, "runner-1", data_dir=tmp_path)

    assert result is not None
    assert result.id == queued.id
    assert result.status == "completed"
    assert result.run_log_path is not None
    assert Path(result.run_log_path).exists()
    assert store.get_runner("runner-1").current_run_id is None  # type: ignore[union-attr]


def test_runner_cancel_queued_run(tmp_path: Path) -> None:
    workflow_path = tmp_path / "queued.toml"
    _write_pass_workflow(workflow_path)
    store = RunnerQueueStore(tmp_path)
    queued = store.enqueue("queued", workflow_path)

    canceled = store.cancel_run(queued.id)

    assert canceled.status == "canceled"
    assert canceled.message == "Canceled before dispatch"


def test_runner_marks_lost_runner_runs(tmp_path: Path) -> None:
    workflow_path = tmp_path / "queued.toml"
    _write_pass_workflow(workflow_path)
    store = RunnerQueueStore(tmp_path)
    store.register_runner("runner-1", "local", [], {"provider_clis": []})
    queued = store.enqueue("queued", workflow_path)
    claimed = store.claim_next("runner-1")
    assert claimed is not None
    stale = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    with closing(sqlite3.connect(tmp_path / "runner-queue.db")) as conn, conn:
        conn.execute(
            "UPDATE runners SET last_seen_at = ? WHERE id = ?",
            (stale, "runner-1"),
        )

    store.mark_lost_runs()

    lost = store.get_run(queued.id)
    assert lost is not None
    assert lost.status == "lost_runner"
    assert lost.message == "Runner heartbeat expired"


def test_queue_connections_close_after_commit_and_rollback(tmp_path: Path) -> None:
    import pytest

    store = RunnerQueueStore(tmp_path)
    with store._connect() as connection:
        connection.execute("CREATE TABLE cleanup_probe (value TEXT)")
        connection.execute("INSERT INTO cleanup_probe VALUES ('committed')")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")

    with pytest.raises(RuntimeError, match="abort"):
        with store._connect() as failed:
            failed.execute("INSERT INTO cleanup_probe VALUES ('rolled back')")
            raise RuntimeError("abort")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        failed.execute("SELECT 1")
    with store._connect() as reopened:
        assert [row[0] for row in reopened.execute("SELECT value FROM cleanup_probe")] == [
            "committed"
        ]


@pytest.mark.parametrize("provider,binary", [("antigravity", "agy"), ("grok", "grok")])
@pytest.mark.parametrize("location", ["path", "configured", "nvm", "missing"])
def test_default_runner_discovers_and_claims_supported_provider(
    provider, binary, location, tmp_path, monkeypatch
):
    executable = tmp_path / "nvm" / "versions" / "node" / "v22.12.0" / "bin" / binary
    if location != "missing":
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
    monkeypatch.setenv(
        "NVM_DIR", str(tmp_path / "nvm" if location == "nvm" else tmp_path / "empty")
    )
    monkeypatch.setattr(
        provider_capabilities.shutil,
        "which",
        lambda name: str(executable) if location == "path" and name == binary else None,
    )
    monkeypatch.setattr(
        provider_capabilities,
        "provider_preference",
        lambda name: (
            {"executable": str(executable)} if location == "configured" and name == provider else {}
        ),
    )
    capabilities = default_runner_capabilities([str(tmp_path)])
    assert capabilities["provider_clis"] == ([] if location == "missing" else [provider])
    assert capabilities["workspace_roots"] == [str(tmp_path)]
    assert capabilities["direct_providers"] == ["anthropic_api", "openai_api"]
    store = RunnerQueueStore(tmp_path / "data")
    store.register_runner("test", "Test runner", [], capabilities)
    queued = store.enqueue(
        "provider-workflow",
        tmp_path / "workflow.rattish",
        required_capabilities={"provider_clis": [provider]},
    )
    claimed = store.claim_next("test")
    if location == "missing":
        assert claimed is None
    else:
        assert claimed is not None and claimed.id == queued.id


def test_default_runner_never_advertises_gemini_cli(monkeypatch):
    monkeypatch.setattr(
        "gofer.core.runner.resolve_provider_executable", lambda provider: "/fake/cli"
    )
    assert default_runner_capabilities()["provider_clis"] == [
        "codex",
        "claude_code",
        "cursor",
        "copilot",
        "opencode",
        "antigravity",
        "grok",
    ]
