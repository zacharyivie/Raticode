"""Runs record the provider app version each worker turn and agent step started.

The version is the one provider discovery reads. It is read again only when the
executable's modification time changes, and a failure says when it changed since the
last passing run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from gofer.core import provider_capabilities
from gofer.core.provider_capabilities import (
    ProviderCapability,
    ProviderCapabilityService,
    provider_app_version,
    version_number,
)
from gofer.core.provider_versions import rem_turn_lines, version_changes
from gofer.rattish.artifacts import rattish_asset_root
from gofer.rattish.run_service import run_rattish_file
from gofer.rattish.workspaces import create_registered_workflow
from gofer.ui.api import _rattish_artifact_ui_payload
from gofer.ui.rem_home import RemHome
from gofer.ui.run_failure import failure_text, run_failure_payload
from tests.unit.test_cli_rem_api import backend as backend
from tests.unit.test_cli_rem_api import cli
from tests.unit.test_rattish_provider_fallback import Script, ScriptedProvider
from tests.unit.test_rem_home import force_run, recipe, wait_run
from tests.unit.test_rem_home import home as home

FAILED: Script = (False, "", "The model refused.", 1, {})


def fake_cli(folder: Path, name: str, output: str, stamp: int) -> Path:
    """A provider CLI that prints ``output`` and counts its calls in ``calls-NAME``.

    ``stamp`` sets its modification time, as installing another version would change it.
    """
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(
        f"#!/bin/sh\necho call >> '{folder / f'calls-{name}'}'\necho '{output}'\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    seconds = 1_800_000_000 + stamp
    os.utime(path, ns=(seconds * 10**9, seconds * 10**9))
    return path


def calls(folder: Path, name: str) -> int:
    log = folder / f"calls-{name}"
    return len(log.read_text(encoding="utf-8").splitlines()) if log.exists() else 0


@pytest.fixture
def fake_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A folder first on PATH, with version reads on (tests/conftest.py turns them off)."""
    folder = tmp_path / "bin"
    folder.mkdir()
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(provider_capabilities, "APP_VERSION_READS", True)
    return folder


def test_version_number_reads_the_number_from_each_cli_style() -> None:
    assert version_number("2.3.1 (Claude Code)") == "2.3.1"
    assert version_number("codex-cli 0.156.0") == "0.156.0"
    assert version_number("1.2.0-beta.3\nmore") == "1.2.0-beta.3"
    assert version_number("nightly") == "nightly"
    assert version_number("  ") is None
    assert version_number(None) is None


def test_the_version_is_read_once_and_again_when_the_executable_changes(fake_bin: Path) -> None:
    fake_cli(fake_bin, "claude", "2.3.0 (Claude Code)", stamp=1)

    assert provider_app_version("claude_code") == "2.3.0"
    assert provider_app_version("claude_code") == "2.3.0"
    assert calls(fake_bin, "claude") == 1

    fake_cli(fake_bin, "claude", "2.3.1 (Claude Code)", stamp=2)
    assert provider_app_version("claude_code") == "2.3.1"
    assert provider_app_version("claude_code") == "2.3.1"
    assert calls(fake_bin, "claude") == 2
    # Plain API providers have no app, so no version.
    assert provider_app_version("anthropic_api") is None


def test_a_cached_discovery_result_is_used_without_starting_the_cli(
    fake_bin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = fake_cli(fake_bin, "codex", "codex-cli 0.155.0", stamp=1)
    service = ProviderCapabilityService()
    monkeypatch.setattr(provider_capabilities, "_service", service)
    service._cache[
        provider_capabilities._CacheKey("codex", str(executable), executable.stat().st_mtime_ns)
    ] = provider_capabilities._CacheEntry(
        expires_at=0,  # expired: a version only changes with the executable
        capability=ProviderCapability(
            id="codex",
            display_name="Codex",
            available=True,
            discovery_status="ready",
            version="codex-cli 0.156.0",
        ),
    )

    assert provider_app_version("codex") == "0.156.0"
    assert calls(fake_bin, "codex") == 0


def test_versions_are_off_in_tests_unless_a_test_turns_them_on() -> None:
    assert provider_capabilities.APP_VERSION_READS is False
    assert provider_app_version("claude_code") is None


# Workflow agent steps

AGENT = """Rattish: 1

Workflow:
  name: Ask

Node ask:
  type: agent
  provider: claude-code
  prompt: Say hi.
"""


def _validate(document: dict[str, Any]) -> None:
    schema = json.loads(
        (rattish_asset_root() / "schemas" / "run.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(json.loads(json.dumps(document)))


@pytest.mark.anyio
async def test_a_failed_step_names_its_provider_app_and_the_change_since_the_last_pass(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    base = tmp_path / "data"
    registered = create_registered_workflow(project, "Ask", registry_dir=base)
    registered.entrypoint.write_text(AGENT, encoding="utf-8")
    fake_cli(fake_bin, "claude", "2.3.0 (Claude Code)", stamp=1)

    passed = await run_rattish_file(
        registered.entrypoint,
        data_dir=base,
        subscriptions={"claude_code": ScriptedProvider("claude_code")},
    )
    assert passed.document["status"] == "passed"
    assert passed.document["runs"][0]["provider"]["provider_version"] == "2.3.0"

    fake_cli(fake_bin, "claude", "2.3.1 (Claude Code)", stamp=2)
    failed = await run_rattish_file(
        registered.entrypoint,
        data_dir=base,
        subscriptions={"claude_code": ScriptedProvider("claude_code", [FAILED])},
    )
    assert failed.document["status"] == "failed"
    record = failed.document["runs"][0]
    assert record["provider"]["provider"] == "claude-code"
    assert record["provider"]["provider_version"] == "2.3.1"
    _validate(passed.document)
    _validate(failed.document)

    studio = _rattish_artifact_ui_payload(failed.document, failed.path)
    assert studio["runNodes"]["ask"]["providerApp"] == "Claude Code 2.3.1"

    text = run_failure_payload(str(failed.document["run_id"]), base)["text"]
    model = record["provider"]["model"]
    assert f"Provider: Claude Code 2.3.1, model {model}\n" in text
    assert "Claude Code changed from 2.3.0 to 2.3.1 since the last passing run.\n" in text


@pytest.mark.anyio
async def test_a_failure_with_the_same_version_or_no_passing_run_adds_no_change_line(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    base = tmp_path / "data"
    registered = create_registered_workflow(project, "Ask", registry_dir=base)
    registered.entrypoint.write_text(AGENT, encoding="utf-8")
    fake_cli(fake_bin, "claude", "2.3.0 (Claude Code)", stamp=1)

    first = await run_rattish_file(
        registered.entrypoint,
        data_dir=base,
        subscriptions={"claude_code": ScriptedProvider("claude_code", [FAILED])},
    )
    alone = run_failure_payload(str(first.document["run_id"]), base)["text"]
    assert "Provider: Claude Code 2.3.0" in alone
    assert "since the last passing run" not in alone

    await run_rattish_file(
        registered.entrypoint,
        data_dir=base,
        subscriptions={"claude_code": ScriptedProvider("claude_code")},
    )
    same = await run_rattish_file(
        registered.entrypoint,
        data_dir=base,
        subscriptions={"claude_code": ScriptedProvider("claude_code", [FAILED])},
    )
    text = run_failure_payload(str(same.document["run_id"]), base)["text"]
    assert "Provider: Claude Code 2.3.0" in text
    assert "since the last passing run" not in text


def test_runs_saved_before_versions_show_no_version() -> None:
    document = {"run_id": "r1", "status": "failed", "workflow": {"id": "w", "name": "W"}}
    nodes = {
        "ask": {
            "status": "error",
            "provider": {"provider": "claude-code", "profile": None, "model": "m", "effort": "e"},
        }
    }
    passing = {"runs": [{"node_id": "ask", "provider": {"provider": "claude-code", "model": "m"}}]}
    text = failure_text(document, nodes, "ask", passing=passing)
    assert "Provider: claude-code m\n" in text
    assert "Claude Code" not in text
    assert _rattish_provider_app({"provider": "claude-code", "model": "m"}) == ""


def _rattish_provider_app(provider: dict[str, Any]) -> str:
    from gofer.ui.api import _provider_app

    return _provider_app(provider)


# Rem worker and reviewer turns


def test_each_worker_and_review_turn_records_its_provider_app_version(
    home: RemHome, tmp_path: Path, fake_bin: Path
) -> None:
    fake_cli(fake_bin, "codex", "codex-cli 0.156.0", stamp=1)

    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked", "failed"})
    assert run["state"] == "succeeded"
    with home.store.connect() as db:
        saved = home.store.get(db, "runs", run["id"])
    turns = saved["turns"]
    assert {turn["role"] for turn in turns} == {"execute", "verify"}
    assert all(turn["provider"] == "codex" for turn in turns)
    assert all(turn["provider_version"] == "0.156.0" for turn in turns)
    assert rem_turn_lines(turns) == ["Provider: Codex 0.156.0 (worker, reviewer)"]


def test_rem_runs_show_names_the_provider_apps_and_a_change_since_the_last_pass(
    backend: Any,
) -> None:
    service, project, _ = backend
    task = cli(
        "rem",
        "tasks",
        "create",
        "--title",
        "Nightly check",
        "--type",
        "research",
        "--prompt",
        "Check the build",
        "--project",
        str(project),
    )["data"]["task"]
    first = service.edit_task(task["id"], {"request_id": "first"}, "run")["run"]
    force_run(
        service,
        first["id"],
        state="succeeded",
        turns=[
            {"id": "t1", "role": "execute", "provider": "codex", "provider_version": "0.155.0"},
            {"id": "t2", "role": "verify", "provider": "codex", "provider_version": "0.155.0"},
        ],
    )
    second = service.edit_task(task["id"], {"request_id": "second"}, "run")["run"]
    assert second["previous_success"]["run_id"] == first["id"]
    force_run(
        service,
        second["id"],
        state="failed",
        turns=[
            {"id": "t3", "role": "execute", "provider": "codex", "provider_version": "0.156.0"},
            {"id": "t4", "role": "verify", "provider": "codex", "provider_version": None},
        ],
    )

    shown = cli("rem", "runs", "show", second["id"])["data"]
    assert [turn["provider_version"] for turn in shown["turns"]] == ["0.156.0", None]
    assert shown["provider_version_changes"] == [
        {
            "provider": "codex",
            "from": "0.155.0",
            "to": "0.156.0",
            "text": "Codex changed from 0.155.0 to 0.156.0 since the last passing run.",
        }
    ]
    text = cli("rem", "runs", "show", second["id"], "--output", "text").stdout
    assert "Provider: Codex 0.156.0 (worker)" in text
    assert "Codex changed from 0.155.0 to 0.156.0 since the last passing run." in text

    passed = cli("rem", "runs", "show", first["id"], "--output", "text").stdout
    assert "Provider: Codex 0.155.0 (worker, reviewer)" in passed
    assert "changed from" not in passed
    assert "provider_version_changes" not in cli("rem", "runs", "show", first["id"])["data"]


def test_version_changes_compare_only_providers_both_runs_used() -> None:
    assert version_changes({"codex": "2"}, {"codex": "1", "claude_code": "9"}) == [
        {
            "provider": "codex",
            "from": "1",
            "to": "2",
            "text": "Codex changed from 1 to 2 since the last passing run.",
        }
    ]
    assert version_changes({"claude_code": "2"}, {"codex": "1"}) == []
    assert version_changes({"codex": "1"}, {"codex": "1"}) == []
