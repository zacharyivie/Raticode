"""Saved run inputs and trigger events: what is kept, Resume and Run again with them."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from gofer.rattish.run_service import (
    RattishResumeRefused,
    RattishRunResult,
    rerun_rattish_run,
    rerun_refusal,
    resume_rattish_run,
    resume_refusal,
    run_rattish_file,
)
from gofer.rattish.run_values import (
    MAX_SAVED_EVENTS,
    delete_run_files,
    password_input_names,
    saved_values_path,
    withheld_input_names,
)
from gofer.rattish.runtime import DEFAULT_NODE_HANDLERS, HandlerResult, NodeHandlerRegistry
from gofer.rattish.workspaces import RegisteredWorkflow, create_registered_workflow

WITH_INPUTS = """Rattish: 1
Workflow:
  name: Deploy
  inputs:
    target:
      schema: {"type": "string"}
      required: true
    token:
      schema: {"type": "string", "format": "password"}
      required: false
    options:
      schema: {"type": "object", "properties": {"pin": {"type": "string", "format": "password"}}}
      required: false
Node build:
  type: bash-command
  command: build
  to: ship
Node ship:
  type: bash-command
  command: ship
  needs: build
"""


class Fake:
    """Records the inputs and trigger events each node saw, and fails nodes on demand."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, dict[str, Any], list[dict[str, Any]]]] = []
        self.fail: set[str] = set()

    async def __call__(self, node, context, bindings) -> HandlerResult:
        self.seen.append(
            (
                node["id"],
                dict(context.workflow_inputs),
                [dict(event) for event in context.trigger_events],
            )
        )
        result = {"stdout": node["id"], "stderr": "", "exit_code": 0, "truncated": False}
        if node["id"] in self.fail:
            self.fail.discard(node["id"])
            return HandlerResult(False, {**result, "exit_code": 1})
        return HandlerResult(True, result)

    def handlers(self) -> NodeHandlerRegistry:
        return NodeHandlerRegistry(
            {**DEFAULT_NODE_HANDLERS._handlers, "raticode.bash_command": self}
        )


def workflow(tmp_path: Path, source: str = WITH_INPUTS) -> tuple[Path, RegisteredWorkflow]:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    base = tmp_path / "data"
    registered = create_registered_workflow(project, "Deploy", registry_dir=base)
    registered.entrypoint.write_text(source, encoding="utf-8")
    return base, registered


async def run(base: Path, registered: RegisteredWorkflow, fake: Fake, **options: Any):
    return await run_rattish_file(
        registered.entrypoint, data_dir=base, handlers=fake.handlers(), **options
    )


def saved(result: RattishRunResult) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(
        saved_values_path(result.path).read_text(encoding="utf-8")
    )
    return document


@pytest.mark.anyio
async def test_inputs_and_events_are_saved_owner_only_without_passwords(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    fake = Fake()
    events = [{"type": "webhook", "trigger_id": "t1", "payload": {"ref": "main"}}]
    result = await run(
        base,
        registered,
        fake,
        workflow_inputs={"target": "prod", "token": "hunter2", "options": {"pin": "1234"}},
        trigger_events=events,
    )
    assert result.status == "passed"
    assert result.document["saved_values"] == {
        "saved": True,
        "inputs": ["target"],
        "withheld_inputs": ["options", "token"],
        "trigger_events": 1,
    }
    path = saved_values_path(result.path)
    assert path.parent == result.path.parent.resolve()
    assert path.name == f"{result.document['run_id']}.inputs"
    document = saved(result)
    assert document["inputs"] == {"target": "prod"}
    assert document["trigger_events"] == events
    raw = path.read_text(encoding="utf-8") + result.path.read_text(encoding="utf-8")
    assert "hunter2" not in raw and "1234" not in raw
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # The run list reads *.json records; the values file is not one of them.
    assert sorted(p.name for p in result.path.parent.glob("*.json")) == [result.path.name]


def test_password_inputs_are_found_anywhere_in_the_schema() -> None:
    ir = {
        "workflow": {
            "inputs": [
                {"name": "a", "schema": {"type": "string"}},
                {"name": "b", "schema": {"type": "string", "format": "password"}},
                {"name": "c", "schema": {"anyOf": [{"type": "null"}, {"format": "password"}]}},
            ]
        }
    }
    assert password_input_names(ir) == {"b", "c"}


@pytest.mark.anyio
async def test_a_run_with_inputs_resumes_and_asks_for_withheld_ones(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    fake = Fake()
    fake.fail.add("ship")
    events = [{"type": "file", "kind": "modified", "path": "/w/a.txt", "trigger_id": "f"}]
    failed = await run(
        base,
        registered,
        fake,
        workflow_inputs={"target": "prod", "token": "hunter2"},
        trigger_events=events,
    )
    assert failed.status == "failed"
    assert resume_refusal(failed.document, failed.compiled.ir) is None
    assert withheld_input_names(failed.document) == ["token"]

    with pytest.raises(RattishResumeRefused, match="Enter token again"):
        await resume_rattish_run(
            registered.entrypoint, failed.document, data_dir=base, handlers=fake.handlers()
        )
    with pytest.raises(RattishResumeRefused, match="not: target"):
        await resume_rattish_run(
            registered.entrypoint,
            failed.document,
            data_dir=base,
            handlers=fake.handlers(),
            resume_inputs={"token": "x", "target": "staging"},
        )
    fake.seen.clear()
    resumed = await resume_rattish_run(
        registered.entrypoint,
        failed.document,
        data_dir=base,
        handlers=fake.handlers(),
        resume_inputs={"token": "hunter2"},
    )
    assert resumed.status == "passed"
    # Only the failed node ran again, with the saved input, the given password and the events.
    assert fake.seen == [("ship", {"target": "prod", "token": "hunter2"}, events)]
    assert resumed.document["resumed_from"]["run_id"] == failed.document["run_id"]
    assert resumed.document["started_by"] == "manual"
    # The resumed run saved its own values, so it could be resumed in turn.
    assert saved(resumed)["inputs"] == {"target": "prod"}


@pytest.mark.anyio
async def test_run_again_uses_the_same_inputs_and_events(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    fake = Fake()
    events = [{"type": "webhook", "trigger_id": "t1", "payload": {"n": 1}}]
    first = await run(
        base,
        registered,
        fake,
        workflow_inputs={"target": "prod"},
        trigger_events=events,
        started_by="trigger",
    )
    assert rerun_refusal(first.document) is None
    fake.seen.clear()
    again = await rerun_rattish_run(
        registered.entrypoint, first.document, data_dir=base, handlers=fake.handlers()
    )
    assert again.status == "passed"
    assert again.document["rerun_of"] == first.document["run_id"]
    assert again.document["started_by"] == "manual"
    assert again.document["trigger_event_count"] == 1
    assert [seen[1:] for seen in fake.seen] == [({"target": "prod"}, events)] * 2

    # A password input is asked for again.
    secret = await run(base, registered, fake, workflow_inputs={"target": "a", "token": "t"})
    with pytest.raises(RattishResumeRefused, match="Enter token again"):
        await rerun_rattish_run(
            registered.entrypoint, secret.document, data_dir=base, handlers=fake.handlers()
        )
    rerun = await rerun_rattish_run(
        registered.entrypoint,
        secret.document,
        data_dir=base,
        handlers=fake.handlers(),
        inputs={"token": "t2"},
        started_by="phone",
    )
    assert rerun.status == "passed" and rerun.document["started_by"] == "phone"


@pytest.mark.anyio
async def test_values_that_cannot_be_used_are_refused_with_a_reason(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    fake = Fake()
    old = await run(base, registered, fake, workflow_inputs={"target": "prod"})
    legacy = {key: value for key, value in old.document.items() if key != "saved_values"}
    assert "before Raticode saved them" in str(rerun_refusal(legacy))
    assert "before Raticode saved events" in str(
        rerun_refusal({**legacy, "input_names": [], "trigger_event_count": 2})
    )

    too_many = [{"type": "file", "path": f"/w/{i}"} for i in range(MAX_SAVED_EVENTS + 1)]
    big = await run(
        base, registered, fake, workflow_inputs={"target": "x"}, trigger_events=too_many
    )
    assert big.document["saved_values"]["saved"] is False
    assert not saved_values_path(big.path).exists()
    assert "more than the 256" in str(rerun_refusal(big.document))

    huge = [{"type": "webhook", "payload": "x" * (1024 * 1024 + 1)}]
    large = await run(base, registered, fake, workflow_inputs={"target": "x"}, trigger_events=huge)
    assert "larger than 1 MiB" in str(rerun_refusal(large.document))

    invalid = await run(base, registered, fake, workflow_inputs={"target": 3})
    assert invalid.status == "invalid_inputs"
    assert "saved_values" not in invalid.document
    assert not saved_values_path(invalid.path).exists()
    assert "were invalid" in str(rerun_refusal(invalid.document))

    # A values file removed from under the run is reported, not guessed at.
    saved_values_path(old.path).unlink()
    with pytest.raises(RattishResumeRefused, match="missing"):
        await rerun_rattish_run(
            registered.entrypoint, old.document, data_dir=base, handlers=fake.handlers()
        )


@pytest.mark.anyio
async def test_a_run_without_inputs_saves_nothing_and_reruns(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path, WITH_INPUTS.replace("required: true", "default: dev"))
    fake = Fake()
    first = await run(base, registered, fake)
    assert "saved_values" not in first.document
    assert not saved_values_path(first.path).exists()
    again = await rerun_rattish_run(
        registered.entrypoint, first.document, data_dir=base, handlers=fake.handlers()
    )
    assert again.status == "passed"


@pytest.mark.anyio
async def test_deleting_a_run_deletes_its_saved_values(tmp_path: Path) -> None:
    base, registered = workflow(tmp_path)
    fake = Fake()
    result = await run(base, registered, fake, workflow_inputs={"target": "prod"})
    values = saved_values_path(result.path)
    assert values.exists()
    delete_run_files(result.path)
    assert not result.path.exists() and not values.exists()
    delete_run_files(result.path)  # already gone: nothing to do

    # A run deleted some other way loses its saved values when the next run ends.
    other = await run(base, registered, fake, workflow_inputs={"target": "prod"})
    other.path.unlink()
    assert saved_values_path(other.path).exists()
    # Values younger than the grace period may belong to a run still starting.
    import os

    from gofer.rattish.run_values import ORPHAN_GRACE_SECONDS

    aged = saved_values_path(other.path).stat().st_mtime - ORPHAN_GRACE_SECONDS - 5
    os.utime(saved_values_path(other.path), (aged, aged))
    await run(base, registered, fake, workflow_inputs={"target": "next"})
    assert not saved_values_path(other.path).exists()


@pytest.mark.anyio
async def test_the_desktop_api_reruns_by_run_id_and_resumes_with_given_passwords(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import functools

    import gofer.ui.api as api

    base, registered = workflow(tmp_path)
    fake = Fake()
    handlers = fake.handlers()
    monkeypatch.setattr(
        api, "rerun_rattish_run", functools.partial(rerun_rattish_run, handlers=handlers)
    )
    monkeypatch.setattr(
        api, "resume_rattish_run", functools.partial(resume_rattish_run, handlers=handlers)
    )
    fake.fail.add("ship")
    failed = await run(base, registered, fake, workflow_inputs={"target": "prod", "token": "pw"})
    run_id = failed.document["run_id"]
    assert api.rattish_run_workflow_id(run_id, base) == registered.workflow_id
    with pytest.raises(api.WorkflowRunError, match="not found"):
        api.rattish_run_workflow_id("20990101T000000000000Z-000000000000", base)

    listed = api.recent_rattish_runs_payload(registered.workflow_id, base, with_resume=True)
    summary = listed["runs"][0]
    assert summary["rerun"] == {"allowed": True, "reason": "", "needsInputs": ["token"]}
    assert summary["resume"]["needsInputs"] == ["token"]

    with pytest.raises(api.WorkflowRunError, match="Enter token again"):
        await api.rerun_workflow_payload(run_id, base)
    again = await api.rerun_workflow_payload(run_id, base, inputs={"token": "pw"})
    assert again["rerunOf"] == run_id and again["status"] == "success"

    resumed = await api.resume_workflow_payload(
        registered.workflow_id, base, run_id=run_id, inputs={"token": "pw"}
    )
    assert resumed["status"] == "success" and resumed["resumedFrom"] == run_id


@pytest.mark.anyio
async def test_retention_clean_up_deletes_saved_values_with_the_run(tmp_path: Path) -> None:
    from gofer.rattish.run_retention import prune_run_records

    base, registered = workflow(tmp_path)
    fake = Fake()
    old = await run(base, registered, fake, workflow_inputs={"target": "old"})
    kept = await run(base, registered, fake, workflow_inputs={"target": "new"})
    result = prune_run_records(
        base, registered.workflow_id, old.path.parent, keep_last=1, keep_days=0, dry_run=False
    )
    assert result.deleted == [old.document["run_id"]]
    assert not old.path.exists() and not saved_values_path(old.path).exists()
    assert saved_values_path(kept.path).exists()


def test_leftover_saved_values_wait_out_a_starting_run_before_they_go(tmp_path: Path) -> None:
    import os
    import time

    from gofer.rattish.run_values import ORPHAN_GRACE_SECONDS, remove_orphaned_values

    # A run saves its values just before its first record, so another run ending at that
    # moment must not take them for a deleted run's leftovers.
    starting = tmp_path / "20261115T000000Z-new.inputs"
    starting.write_text("{}")
    old = tmp_path / "20261101T000000Z-old.inputs"
    old.write_text("{}")
    stale = time.time() - ORPHAN_GRACE_SECONDS - 5
    os.utime(old, (stale, stale))
    kept = tmp_path / "20261101T000000Z-kept.inputs"
    kept.write_text("{}")
    os.utime(kept, (stale, stale))
    (tmp_path / "20261101T000000Z-kept.json").write_text("{}")

    assert remove_orphaned_values(tmp_path) == ["20261101T000000Z-old"]
    assert starting.exists() and kept.exists() and not old.exists()
