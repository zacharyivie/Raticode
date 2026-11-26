"""Opt-in result cache for common-llm-task and jev nodes (``cache: DURATION``).

A cached activation makes no provider call, shows as Cached in the run timeline and
records 0 tokens and $0. Entries are owner-only, capped per workflow and cleared from
Workflow settings or ``rattish cache clear``. Test sets never use the cache.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.rattish import result_cache as result_cache_module
from gofer.rattish import workflow_secrets
from gofer.rattish.artifacts import compile_rattish_source, rattish_asset_root
from gofer.rattish.diagnostics import RattishError
from gofer.rattish.result_cache import (
    MAX_WORKFLOW_BYTES,
    ResultCache,
    cache_duration_seconds,
    workflow_cache_dir,
)
from gofer.rattish.run_service import run_rattish_file
from gofer.rattish.run_usage import usage_line, usage_payload
from gofer.rattish.test_sets import (
    prepare_test_run,
    run_test_set,
    save_test_set,
    workflow_tests_file,
)
from gofer.rattish.workflow_runtime import execute_workflow
from gofer.ui import api as ui_api
from tests.unit.test_rattish_jev import FakeJev, fake_client, noul
from tests.unit.test_rattish_provider_fallback import ScriptedProvider
from tests.unit.test_rattish_workflow_secrets import MemoryStore
from tests.unit.test_ui_server import _request

runner = CliRunner()
ANSWER = (
    True,
    "Cached summary.",
    None,
    0,
    {"total_cost_usd": 0.02, "input_tokens": 30, "output_tokens": 12},
)


def _write(tmp_path: Path, text: str, name: str = "project") -> Path:
    source = tmp_path / name / "workflow.rattish"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(text, encoding="utf-8")
    return source


def _llm_source(node_fields: str = "  cache: 7d\n", target: str = "hello world") -> str:
    return (
        "Rattish: 1\n\nWorkflow:\n  name: Cache Demo\n\n"
        "Node summarize:\n  type: common-llm-task\n  provider: claude-code\n"
        f"  target: {target}\n" + node_fields
    )


def _compile(source: Path) -> Any:
    return compile_rattish_source(
        source.read_text(encoding="utf-8"), source, project_root=source.parent
    )


def _validate_record(document: dict[str, Any]) -> None:
    schema = json.loads(
        (rattish_asset_root() / "schemas" / "run.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(json.loads(json.dumps(document)))


async def _run(source: Path, data_dir: Path, provider: ScriptedProvider, **kwargs: Any) -> Any:
    result = await run_rattish_file(
        source, data_dir=data_dir, subscriptions={"claude_code": provider}, **kwargs
    )
    _validate_record(result.document)
    return result


# Durations and compiler rules


def test_durations_parse_and_reject_other_spellings() -> None:
    assert cache_duration_seconds("90s") == 90
    assert cache_duration_seconds("15m") == 900
    assert cache_duration_seconds("12h") == 43200
    assert cache_duration_seconds("7d") == 604800
    assert cache_duration_seconds("2w") == 1209600
    for bad in ("0d", "7", "7 d", "1y", "-1d", "", None, 7):
        assert cache_duration_seconds(bad) is None


def test_llm_task_and_jev_nodes_compile_cache_and_default_to_none(tmp_path: Path) -> None:
    source = _write(
        tmp_path,
        _llm_source() + "\nNode ask:\n  type: jev\n  question: Is it a greeting?\n  cache: 12h\n"
        "  criteria:\n    yes: A greeting.\n    no: Anything else.\n"
        "\nNode plain:\n  type: common-llm-task\n  provider: claude-code\n",
    )

    nodes = {node["id"]: node for node in _compile(source).ir["nodes"]}

    assert nodes["summarize"]["configuration"]["cache"] == "7d"
    assert nodes["ask"]["configuration"]["cache"] == "12h"
    assert nodes["plain"]["configuration"]["cache"] is None


def test_agent_nodes_cannot_cache_because_they_change_files(tmp_path: Path) -> None:
    source = _write(
        tmp_path,
        "Rattish: 1\n\nWorkflow:\n  name: Agent\n\n"
        "Node edit:\n  type: agent\n  provider: claude-code\n  prompt: Fix it.\n  cache: 7d\n",
    )

    with pytest.raises(RattishError) as caught:
        _compile(source)

    messages = [item.message for item in caught.value.diagnostics if item.severity == "error"]
    assert messages == [
        "Agent node 'edit' can't use cache, because an agent can change files. "
        "Use cache on a common-llm-task or jev node."
    ]


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ("  cache: 400d\n", "cache can be at most 365d"),
        ("  cache: 7y\n", "configuration is invalid"),
        ("  cache: 7d\n  memory: run\n", "can't use cache with memory: run"),
    ],
    ids=["too-long", "bad-unit", "memory"],
)
def test_invalid_cache_settings_do_not_compile(tmp_path: Path, fields: str, expected: str) -> None:
    with pytest.raises(RattishError) as caught:
        _compile(_write(tmp_path, _llm_source(fields)))

    assert any(expected in item.message for item in caught.value.diagnostics), [
        item.message for item in caught.value.diagnostics
    ]


# Runtime


async def test_a_second_run_reuses_the_answer_with_no_call_and_zero_usage(tmp_path: Path) -> None:
    source = _write(tmp_path, _llm_source())
    data_dir = tmp_path / "data"
    provider = ScriptedProvider("claude-code", [ANSWER])

    first = await _run(source, data_dir, provider)
    second = await _run(source, data_dir, provider)

    assert len(provider.calls) == 1
    assert first.document["outputs"] == second.document["outputs"] == {}
    fresh, cached = first.document["runs"][0], second.document["runs"][0]
    assert fresh["output"] == cached["output"] == "Cached summary."
    assert "cached_at" not in fresh
    assert datetime.fromisoformat(cached["cached_at"]) <= datetime.now(UTC)
    assert fresh["usage"]["cost_usd"] == pytest.approx(0.02)
    assert cached["usage"] == {
        "calls": 0,
        "reported_calls": 0,
        "partial_calls": 0,
        "unknown_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "cost_reported_calls": 0,
        "cost_unknown_calls": 0,
        "cached_results": 1,
    }
    run_usage = second.document["usage"]
    assert (run_usage["calls"], run_usage["total_tokens"], run_usage["cost_usd"]) == (0, 0, 0.0)
    assert usage_line(run_usage) == "No model calls, 1 cached result, tokens 0, estimated cost $0"
    payload = usage_payload(run_usage)
    assert payload is not None
    assert (payload["tokensLabel"], payload["costLabel"], payload["cachedResults"]) == (
        "0",
        "$0",
        1,
    )
    completed = [event for event in second.document["events"] if event["type"] == "node_completed"]
    assert completed[0]["cached"] is True
    assert not any(
        event.get("cached")
        for event in first.document["events"]
        if event["type"] == "node_completed"
    )


async def test_the_run_timeline_shows_a_cached_step_as_cached(tmp_path: Path) -> None:
    source = _write(tmp_path, _llm_source())
    data_dir = tmp_path / "data"
    provider = ScriptedProvider("claude-code", [ANSWER])
    await _run(source, data_dir, provider)
    second = await _run(source, data_dir, provider)

    payload = ui_api._rattish_artifact_ui_payload(second.document, second.path)

    completed = [event for event in payload["runEvents"] if event["type"] == "node_completed"]
    assert completed[0]["status"] == "cached"
    assert completed[0]["cached"] is True
    assert "cached result" in completed[0]["message"]
    node = payload["runNodes"]["summarize"]
    assert node["cached"] is True
    assert node["cachedAt"] == second.document["runs"][0]["cached_at"]
    assert node["usage"]["tokensLabel"] == "0"
    assert node["usage"]["costLabel"] == "$0"


@pytest.mark.parametrize(
    "change",
    [
        lambda text: text.replace("hello world", "goodbye world"),
        lambda text: text.replace(
            "provider: claude-code\n", "provider: claude-code\n  model: claude-haiku-5\n"
        ),
        lambda text: text.replace(
            "provider: claude-code\n", "provider: claude-code\n  effort: low\n"
        ),
        lambda text: text.replace("  cache: 7d\n", "  cache: 7d\n  task: review\n"),
        lambda text: text + '  output-schema: {"type": "object"}\n  repair-attempts: 0\n',
    ],
    ids=["prompt", "model", "effort", "task", "output-schema"],
)
async def test_a_changed_prompt_model_effort_or_schema_calls_the_provider_again(
    tmp_path: Path, change: Any
) -> None:
    source = _write(tmp_path, _llm_source())
    data_dir = tmp_path / "data"
    answers = [ANSWER, (True, '{"ok": true}', None, 0, {})]
    provider = ScriptedProvider("claude-code", answers)
    await _run(source, data_dir, provider)

    source.write_text(change(source.read_text(encoding="utf-8")), encoding="utf-8")
    changed = await _run(source, data_dir, provider)

    assert len(provider.calls) == 2
    assert "cached_at" not in changed.document["runs"][0]


async def test_moving_the_node_or_changing_the_duration_keeps_the_entry(tmp_path: Path) -> None:
    source = _write(tmp_path, _llm_source())
    data_dir = tmp_path / "data"
    provider = ScriptedProvider("claude-code", [ANSWER])
    await _run(source, data_dir, provider)

    text = source.read_text(encoding="utf-8").replace("cache: 7d", "cache: 30d")
    text = text.replace("Node summarize:", "# A comment moves the node.\n\nNode summarize:")
    source.write_text(text, encoding="utf-8")
    again = await _run(source, data_dir, provider)

    assert len(provider.calls) == 1
    assert "cached_at" in again.document["runs"][0]


async def test_bound_inputs_are_part_of_the_key(tmp_path: Path) -> None:
    source = _write(
        tmp_path,
        "Rattish: 1\n\nWorkflow:\n  name: Inputs\n  inputs:\n    topic:\n"
        '      schema: {"type": "string"}\n      required: true\n\n'
        "Node summarize:\n  type: common-llm-task\n  provider: claude-code\n  cache: 1d\n"
        "  with:\n    target: input.topic\n",
    )
    data_dir = tmp_path / "data"
    provider = ScriptedProvider("claude-code")

    for topic in ("cats", "dogs", "cats"):
        await _run(source, data_dir, provider, workflow_inputs={"topic": topic})

    assert len(provider.calls) == 2


async def test_a_file_input_counts_by_its_contents_not_its_run_copy(tmp_path: Path) -> None:
    source = _write(
        tmp_path,
        "Rattish: 1\n\nWorkflow:\n  name: Files\n  inputs:\n    doc:\n"
        '      schema: {"type": "string", "format": "file"}\n      required: true\n\n'
        "Node summarize:\n  type: common-llm-task\n  provider: claude-code\n  cache: 7d\n"
        "  with:\n    target: input.doc\n",
    )
    data_dir = tmp_path / "data"
    provider = ScriptedProvider("claude-code")
    first_file = tmp_path / "a" / "notes.txt"
    same_bytes = tmp_path / "b" / "notes.txt"
    for path in (first_file, same_bytes):
        path.parent.mkdir()
        path.write_bytes(b"The same notes.\n")

    first = await _run(source, data_dir, provider, workflow_inputs={"doc": str(first_file)})
    again = await _run(source, data_dir, provider, workflow_inputs={"doc": str(same_bytes)})

    # Each run bound the input to its own copy, so the paths differ but the bytes don't.
    copies = {run.document["run_id"]: run.path for run in (first, again)}
    assert len(copies) == 2
    assert len(provider.calls) == 1
    assert "cached_at" not in first.document["runs"][0]
    assert "cached_at" in again.document["runs"][0]

    first_file.write_bytes(b"Different notes.\n")
    changed = await _run(source, data_dir, provider, workflow_inputs={"doc": str(first_file)})

    assert len(provider.calls) == 2
    assert "cached_at" not in changed.document["runs"][0]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


async def test_a_worktree_run_counts_by_its_project_not_its_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{name}_NAME", "t")
        monkeypatch.setenv(f"GIT_{name}_EMAIL", "t@x")
    repo = tmp_path / "project"
    (repo / "sub").mkdir(parents=True)
    (repo / "sub" / "keep").write_text("")
    _write(
        tmp_path,
        "Rattish: 1\n\nWorkflow:\n  name: Worktree Cache\n  workspace: worktree\n\n"
        "Node summarize:\n  type: common-llm-task\n  provider: claude-code\n"
        "  target: hello world\n  cache: 7d\n"
        f"\nNode nested:\n  type: common-llm-task\n  provider: claude-code\n"
        f"  target: the sub folder\n  working-dir: {repo}/sub\n  cache: 7d\n",
    )
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    data_dir = tmp_path / "data"
    provider = ScriptedProvider("claude-code")

    first = await _run(repo / "workflow.rattish", data_dir, provider)
    second = await _run(repo / "workflow.rattish", data_dir, provider)

    checkouts = {run.document["worktree"]["checkout"] for run in (first, second)}
    assert len(checkouts) == 2
    assert first.status == second.status == "passed"
    assert len(provider.calls) == 2
    assert all("cached_at" not in run for run in first.document["runs"])
    assert all("cached_at" in run for run in second.document["runs"])


async def test_an_entry_older_than_the_duration_is_not_used(tmp_path: Path) -> None:
    source = _write(tmp_path, _llm_source("  cache: 1h\n"))
    data_dir = tmp_path / "data"
    provider = ScriptedProvider("claude-code")
    await _run(source, data_dir, provider)
    (entry,) = workflow_cache_dir(data_dir, "project").glob("*.json")
    document = json.loads(entry.read_text(encoding="utf-8"))
    document["created_at"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    entry.write_text(json.dumps(document), encoding="utf-8")

    stale = await _run(source, data_dir, provider)

    assert len(provider.calls) == 2
    assert "cached_at" not in stale.document["runs"][0]


async def test_failures_are_never_cached(tmp_path: Path) -> None:
    source = _write(tmp_path, _llm_source())
    data_dir = tmp_path / "data"
    provider = ScriptedProvider("claude-code", [(False, "", "boom", 1, {}), ANSWER])

    first = await _run(source, data_dir, provider)
    second = await _run(source, data_dir, provider)

    assert first.status == "failed"
    assert second.status == "passed"
    assert "cached_at" not in second.document["runs"][0]
    assert len(provider.calls) == 2


async def test_nodes_that_read_a_secret_never_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = MemoryStore()
    monkeypatch.setattr(workflow_secrets, "_secret_store", lambda: memory)
    monkeypatch.setenv("CACHE_TEST_TOKEN", "do-not-store-me")
    source = _write(
        tmp_path,
        _llm_source() + '  with:\n    instructions: secret["CACHE_TEST_TOKEN"]\n',
    )
    data_dir = tmp_path / "data"
    provider = ScriptedProvider("claude-code")

    await _run(source, data_dir, provider)
    await _run(source, data_dir, provider)

    assert len(provider.calls) == 2
    assert not workflow_cache_dir(data_dir, "project").exists()


async def test_test_sets_bypass_the_cache(tmp_path: Path) -> None:
    source = _write(tmp_path, _llm_source())
    save_test_set(
        workflow_tests_file(source),
        {
            "testSetVersion": 1,
            "cases": [
                {"id": "one", "checks": [{"type": "json", "target": "status", "equals": "passed"}]}
            ],
        },
    )
    data_dir = tmp_path / "data"
    provider = ScriptedProvider("claude-code")
    await _run(source, data_dir, provider)
    entries = sorted(workflow_cache_dir(data_dir, "project").glob("*.json"))
    assert len(entries) == 1
    before = entries[0].read_bytes()

    result = await run_test_set(
        prepare_test_run(source, data_dir=data_dir), subscriptions={"claude_code": provider}
    )

    assert result["cases"][0]["status"] == "passed", result["cases"][0]
    assert len(provider.calls) == 2
    assert sorted(workflow_cache_dir(data_dir, "project").glob("*.json")) == entries
    assert entries[0].read_bytes() == before


async def test_jev_nodes_reuse_their_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.classifiers import jev_settings

    fake = FakeJev(noul(0.9))
    monkeypatch.setattr(
        jev_settings, "default_jev_client", lambda data_dir=None: fake_client(fake, data_dir)
    )
    ir = _compile(
        _write(
            tmp_path,
            "Rattish: 1\n\nWorkflow:\n  name: Jev\n\nNode ask:\n  type: jev\n"
            "  question: Is it a greeting?\n  state: hello\n  cache: 1d\n"
            "  criteria:\n    yes: A greeting.\n    no: Anything else.\n",
        )
    ).ir
    cache = ResultCache(tmp_path / "data")

    first = await execute_workflow(ir, data_dir=tmp_path / "data", result_cache=cache)
    second = await execute_workflow(ir, data_dir=tmp_path / "data", result_cache=cache)
    uncached = await execute_workflow(ir, data_dir=tmp_path / "data")

    assert len(fake.requests) == 2
    assert first.runs[0].result.cached_at is None
    assert second.runs[0].result.cached_at is not None
    assert second.runs[0].result.output == first.runs[0].result.output
    assert second.runs[0].usage is not None and second.runs[0].usage["cost_usd"] == 0.0
    assert uncached.runs[0].result.cached_at is None


# The cache store


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_entries_and_folders_are_owner_only_even_with_a_wide_umask(tmp_path: Path) -> None:
    old = os.umask(0o002)
    try:
        cache = ResultCache(tmp_path)
        assert cache.store("demo", "a" * 64, "node", {"x": 1}, 60)
    finally:
        os.umask(old)

    root = tmp_path / "rattish-cache"
    for folder in (root, root / "demo"):
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    entry = root / "demo" / f"{'a' * 64}.json"
    assert stat.S_IMODE(entry.stat().st_mode) == 0o600
    assert cache.lookup("demo", "a" * 64, 60) is not None


def test_workflows_never_share_entries_and_invalid_names_are_refused(tmp_path: Path) -> None:
    cache = ResultCache(tmp_path)
    cache.store("one", "b" * 64, "node", "answer", 60)

    assert cache.lookup("two", "b" * 64, 60) is None
    with pytest.raises(ValueError):
        cache.store("../escape", "b" * 64, "node", "answer", 60)
    with pytest.raises(ValueError):
        cache.lookup("one", "../../etc/passwd", 60)


def test_the_size_cap_removes_the_entries_expiring_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(result_cache_module, "MAX_WORKFLOW_BYTES", 600)
    cache = ResultCache(tmp_path)
    start = datetime.now(UTC)
    keys = [f"{index:x}" * 64 for index in range(1, 6)]
    for offset, key in enumerate(keys):
        cache.store("demo", key, "node", "x" * 100, 3600 + offset * 60, now=start)

    kept = {path.stem for path in workflow_cache_dir(tmp_path, "demo").glob("*.json")}

    assert keys[-1] in kept
    assert keys[0] not in kept
    assert cache.stats("demo").bytes <= 600


def test_expired_entries_are_pruned_and_oversized_answers_are_not_saved(tmp_path: Path) -> None:
    cache = ResultCache(tmp_path)
    past = datetime.now(UTC) - timedelta(days=2)
    cache.store("demo", "c" * 64, "node", "old", 60, now=past)
    assert cache.stats("demo").entries == 0

    cache.store("demo", "d" * 64, "node", "new", 60)
    names = {path.stem for path in workflow_cache_dir(tmp_path, "demo").glob("*.json")}
    assert names == {"d" * 64}
    assert not cache.store("demo", "e" * 64, "node", "x" * (3 * 1024 * 1024), 60)
    assert cache.stats("demo").max_bytes == MAX_WORKFLOW_BYTES


def test_a_damaged_entry_reads_as_a_miss(tmp_path: Path) -> None:
    cache = ResultCache(tmp_path)
    cache.store("demo", "f" * 64, "node", "answer", 60)
    entry = workflow_cache_dir(tmp_path, "demo") / f"{'f' * 64}.json"
    entry.write_text("{not json", encoding="utf-8")

    assert cache.lookup("demo", "f" * 64, 60) is None


def test_clear_removes_one_workflow_or_all(tmp_path: Path) -> None:
    cache = ResultCache(tmp_path)
    cache.store("one", "1" * 64, "node", "a", 60)
    cache.store("one", "2" * 64, "node", "b", 60)
    cache.store("two", "3" * 64, "node", "c", 60)

    assert cache.clear("one") == 2
    assert not workflow_cache_dir(tmp_path, "one").exists()
    assert cache.stats("two").entries == 1
    assert cache.clear_all() == 1
    assert cache.stats("two").entries == 0


# Workflow settings and the CLI


def test_workflow_settings_shows_and_clears_the_cache(tmp_path: Path) -> None:
    ResultCache(tmp_path).store("demo", "a" * 64, "node", "answer", 60)

    shown = cast(
        dict[str, Any], _request(tmp_path, "GET", "/api/rattish/cache?workflowId=demo").json()
    )
    assert shown["entries"] == 1 and shown["bytes"] > 0
    assert shown["maxBytes"] == MAX_WORKFLOW_BYTES

    cleared = _request(tmp_path, "DELETE", "/api/rattish/cache?workflowId=demo")
    assert cleared.status == 200
    assert cast(dict[str, Any], cleared.json())["removed"] == 1
    assert ResultCache(tmp_path).stats("demo").entries == 0

    assert _request(tmp_path, "GET", "/api/rattish/cache").status == 400
    assert _request(tmp_path, "GET", "/api/rattish/cache?workflowId=..%2Fx").status == 400
    assert (
        _request(
            tmp_path, "DELETE", "/api/rattish/cache?workflowId=demo", authenticated=False
        ).status
        == 401
    )


def test_rattish_cache_show_and_clear(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    source = _write(tmp_path, _llm_source())
    ResultCache(data_dir).store("project", "a" * 64, "summarize", "answer", 60)
    ResultCache(data_dir).store("other", "b" * 64, "summarize", "answer", 60)

    shown = runner.invoke(
        app,
        ["rattish", "cache", "show", str(source), "--data-dir", str(data_dir), "--format", "json"],
    )
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["entries"] == 1

    cleared = runner.invoke(
        app, ["rattish", "cache", "clear", "--workflow-id", "project", "--data-dir", str(data_dir)]
    )
    assert cleared.exit_code == 0, cleared.output
    assert "Removed 1 saved answer(s) from project's result cache." in cleared.output

    everything = runner.invoke(
        app, ["rattish", "cache", "clear", "--all", "--data-dir", str(data_dir)]
    )
    assert everything.exit_code == 0, everything.output
    assert ResultCache(data_dir).stats("other").entries == 0

    neither = runner.invoke(app, ["rattish", "cache", "clear", "--data-dir", str(data_dir)])
    assert neither.exit_code == 2
