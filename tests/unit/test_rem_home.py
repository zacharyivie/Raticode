from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from gofer.core.provider_capabilities import CLI_PROVIDERS
from gofer.ui.chat_jobs import ChatJobs
from gofer.ui.chat_steering import ChatSteering
from gofer.ui.rem_home import RemHome, Superseded, subject_hash
from gofer.ui.rem_home_models import (
    Recipe,
    RemConflict,
    Schedule,
    completion_recap,
    occurrence_context,
    worker_config,
)
from gofer.ui.rem_home_provider import MalformedDecision
from gofer.ui.rem_home_store import RemHomeStore

# Task card fields the conversation fills in from the run as it is now.
LIVE_CARD = {"state", "needs_user", "step"}


def force_run(home: RemHome, identity: str, **patch: Any) -> None:
    """Test setup: write a run's fields directly, as recovery or a resume would.

    ``RemHome._update`` refuses to change the state of a finished run.
    """
    with home.store.connect() as db:
        run = home.store.get(db, "runs", identity)
        run.update(patch)
        home.store.put(db, "runs", run)


def recipe(project: Path | None = None, **patch: Any) -> dict[str, Any]:
    return {
        "title": "Check the feature",
        "type": "develop",
        "prompt": "Implement the feature",
        "project_dir": str(project) if project else None,
        "schedule": {"kind": "once", "timezone": "UTC"},
        "acceptance_criteria": [{"id": "check", "description": "It works"}],
        **patch,
    }


@pytest.fixture
def home(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    import gofer.ui.rem_home as rem_home_module

    # The automatic worker limit follows this computer's cores and memory (2 on a
    # 4-core CI runner), and new workers wait when memory runs low. Pin both so
    # tests behave the same on every host; admission tests set their own values.
    monkeypatch.setattr(rem_home_module, "memory_available_bytes", lambda: 8 * 1024**3)
    monkeypatch.setattr(rem_home_module, "swap_blocked", lambda: None)

    async def source(**kwargs: Any):
        instructions = kwargs["agent_instructions"]
        if "independent read-only reviewer" in instructions:
            yield {
                "type": "thought",
                "text": "Independent check",
                "trace": {
                    "id": "review-check",
                    "category": "shell",
                    "phase": "result",
                    "status": "completed",
                    "exitCode": 0,
                    "output": "1 passed",
                },
            }
            yield {
                "type": "final",
                "message": {
                    "body": json.dumps(
                        {
                            "criteria": [
                                {
                                    "id": "check",
                                    "result": "pass",
                                    "evidence": "Observed passing test receipt",
                                }
                            ],
                            "feedback": "",
                        }
                    )
                },
            }
        else:
            yield {
                "type": "thought",
                "text": "Tests passed",
                "trace": {
                    "kind": "tool",
                    "category": "shell",
                    "phase": "result",
                    "status": "completed",
                    "exitCode": 0,
                    "output": "1 passed",
                    "input": "pytest tests/test_feature.py",
                },
            }
            yield {"type": "final", "message": {"body": "Implemented and tested."}}

    data = tmp_path_factory.mktemp("rem-data")
    instance = RemHome(
        data,
        source=source,
        jobs=ChatJobs(data),
        steering=ChatSteering(data),
        authorize=lambda c, r: None,
        generate=lambda p, c, e: {"reply": "Hello.", "tasks": [], "memories": []},
        start=False,
    )
    instance.configure(
        {"provider": "codex", "model": "cli-default", "command_slots": {"maxWorkers": 8}}
    )
    yield instance
    instance.close()
    for worker, _ in instance.workers.values():
        worker.join(timeout=5)
    instance.jobs.close()
    instance.steering.close()


def wait_run(home: RemHome, task_id: str, states: set[str], timeout: float = 5) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        home.tick()
        task = next(t for t in home.store.tasks() if t["id"] == task_id)
        if task["last_run"]["state"] in states:
            # The outcome commits first; its chat update follows from the outbox.
            run_id = task["last_run"]["id"]
            while any(e["run_id"] == run_id for e in home.store.outbox()):
                assert time.monotonic() < deadline, home.store.outbox()
                home.deliver_outcomes()
                time.sleep(0.01)
            task = next(t for t in home.store.tasks() if t["id"] == task_id)
            return cast(dict[str, Any], task["last_run"])
        time.sleep(0.01)
    raise AssertionError(home.store.tasks())


def test_recipe_validation_and_recommended_criteria(tmp_path: Path):
    item = Recipe.model_validate(recipe(tmp_path, acceptance_criteria=[]))
    assert item.acceptance_criteria
    assert all(c.authored_by == "rem_recommended" for c in item.acceptance_criteria)
    with pytest.raises(ValueError):
        Recipe.model_validate(recipe(type="maintain"))
    assert Recipe.model_validate(recipe(type="develop")).project_dir is None
    with pytest.raises(ValueError):
        Recipe.model_validate(recipe(tmp_path, type="magic"))
    with pytest.raises(ValueError):
        Schedule(kind="weekly", weekdays=[])
    with pytest.raises(ValueError):
        Schedule(timezone="Wrong/Zone")
    with pytest.raises(ValueError):
        Schedule(kind="cron", cron="bad cron")


@pytest.mark.parametrize("provider", CLI_PROVIDERS)
def test_requested_worker_provider_and_model_reach_threads_and_review(
    home: RemHome, tmp_path: Path, provider: str
):
    observed = []
    original = home.source

    async def source(**kwargs):
        observed.append(kwargs)
        async for event in original(**kwargs):
            yield event

    model = "auto" if provider == "cursor" else "requested-model"
    home.source = source
    home.configure({"provider": "codex", "model": "rem-model", "effort": "high"})

    def generate(prompt, config, cancel):
        assert "provider:cursor and model:auto" in prompt
        assert config["provider"] == "codex" and config["model"] == "rem-model"
        return {
            "reply": "I will start that thread.",
            "tasks": [recipe(tmp_path, provider=provider, model=model)],
        }

    home.generate = generate
    home.submit_message(
        {"request_id": "requested-provider", "body": f"Start a {provider} {model} thread"}
    )
    home._reply()
    task = home.store.tasks()[0]
    assert task["provider"] == provider and task["model"] == model
    # The conversation contains one linked acknowledgement, not the model's duplicate.
    assert len(home.store.conversation()["messages"]) == 2
    assert home.store.conversation()["messages"][-1]["work"]["thread_id"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert len(observed) == 2
    for options in observed:
        assert (options["provider"], options["model"], options["effort"]) == (provider, model, None)
    for identity in (run["thread_id"], run["verification_thread_id"]):
        metadata = home.store.thread(identity)["metadata"]
        assert (metadata["provider"], metadata["model"], metadata["effort"]) == (
            provider,
            model,
            "",
        )
        # Finished task threads stay in the thread list; the desktop marks them unread.
        assert metadata["archived"] is False
    assert home.store.conversation()["conversation"]["model"] == "rem-model"
    assert home.store.conversation()["messages"][-1]["body"] == "Implemented and tested."


def test_rem_chat_uses_its_own_selection_while_workers_keep_theirs(home: RemHome, tmp_path: Path):
    observed = []
    original = home.source

    async def source(**kwargs):
        observed.append(kwargs)
        async for event in original(**kwargs):
            yield event

    home.source = source
    home.configure(
        {
            "provider": "codex",
            "model": "worker-model",
            "effort": "high",
            "chat_provider": "claude_code",
            "chat_model": "chat-model",
            "chat_effort": "low",
        }
    )
    chat_selections = []

    def generate(prompt, config, cancel):
        chat_selections.append((config["provider"], config["model"], config["effort"]))
        if "proactive task update" in prompt:
            return {"summary": "Implemented and tested.", "needs_user": False}
        assert "worker-model" in prompt and "chat-model" not in prompt
        return {"reply": "Starting it.", "tasks": [recipe(tmp_path)]}

    home.generate = generate
    home.submit_message({"request_id": "split-selection", "body": "Build it"})
    home._reply()
    assert home.store.conversation()["conversation"]["model"] == "chat-model"
    task = home.store.tasks()[0]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    for options in observed:
        assert (options["provider"], options["model"]) == ("codex", "worker-model")
    assert chat_selections and all(
        selection == ("claude_code", "chat-model", "low") for selection in chat_selections
    )


def test_rem_chat_selection_follows_workers_until_chosen():
    from gofer.ui.rem_home_models import chat_config

    worker = {"provider": "codex", "model": "m", "effort": "high", "chat_provider": ""}
    assert chat_config(worker) is worker
    assert chat_config({**worker, "chat_provider": "claude_code"})["provider"] == "claude_code"
    chosen = chat_config({**worker, "chat_provider": "claude_code"})
    assert (chosen["model"], chosen["effort"]) == ("cli-default", "")


def test_rem_rejects_an_unknown_chat_provider(home: RemHome):
    with pytest.raises(ValueError, match="chat provider"):
        home.configure({"provider": "codex", "chat_provider": "nope"})


def test_worker_selection_resets_incompatible_defaults_and_preserves_grants():
    base = {
        "provider": "codex",
        "model": "rem-model",
        "effort": "high",
        "projects": [{"root": "/p"}],
        "resources": {"web": False},
    }
    assert worker_config(base, {}) == base
    changed = worker_config(base, {"provider": "cursor"})
    assert (changed["provider"], changed["model"], changed["effort"]) == (
        "cursor",
        "cli-default",
        "",
    )
    changed = worker_config(base, {"model": "another-model", "effort": "low"})
    assert (changed["provider"], changed["model"], changed["effort"]) == (
        "codex",
        "another-model",
        "low",
    )
    assert changed["resources"] == base["resources"] and changed["projects"] == base["projects"]
    assert base["model"] == "rem-model" and base["effort"] == "high"


@pytest.mark.parametrize(
    "selection",
    [
        {"provider": "unknown"},
        {"model": ""},
        {"model": "auto\n"},
        {"effort": " high"},
        {"model": "x" * 257},
    ],
)
def test_invalid_worker_selection_is_rejected(selection):
    with pytest.raises(ValueError):
        Recipe.model_validate(recipe(Path("/p"), **selection))


def test_legacy_task_acknowledgements_keep_their_original_place(home: RemHome):
    with home.store.connect() as db:
        home.store.message(db, "Start a discussion", identity="request:one", role="user")
        task = home.store.add_task(db, Recipe.model_validate(recipe(type="errand")))
        home.store.add_run(db, task, "message:request:one:0", {})
        home.store.message(db, "I'll start a discussion", identity="reply-request:one")
        home.store.message(db, "What is running?", identity="question", role="user")
        home.store.message(db, "Nothing is running", identity="reply-question")
    messages = home.store.conversation()["messages"]
    assert [m["id"] for m in messages][-3:] == ["reply-request:one", "question", "reply-question"]
    assert messages[-1]["body"] == "Nothing is running"
    with home.store.connect() as db:
        assert db.execute("SELECT id FROM messages WHERE id='reply-request:one'").fetchone()


def test_task_completion_appends_after_other_requests_without_changing_replies(
    home: RemHome, tmp_path: Path
):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    with home.store.connect() as db:
        home.store.message(
            db, "What is Theo's latest video?", identity="other-request", role="user"
        )
        home.store.message(db, "I'll check it.", identity="other-reply")
    before = home.store.conversation()["messages"]
    assert home._finish(run["id"], "succeeded", "The audit is ready.", epoch=run["epoch"])
    home.deliver_outcomes()
    after = home.store.conversation()["messages"]

    def written(message: dict[str, Any]) -> dict[str, Any]:
        # A card is served with its run's state now; everything written stays as it was.
        work = {k: v for k, v in (message.get("work") or {}).items() if k not in LIVE_CARD}
        return {**message, "work": work} if "work" in message else message

    assert [written(m) for m in after[:-1]] == [written(m) for m in before]
    assert after[0]["work"]["state"] == "succeeded"
    assert after[-1]["kind"] == "task_update"
    updates = [m for m in after if m.get("work", {}).get("task_id") == task["id"]]
    assert len(updates) == 2
    assert all(m["work"]["thread_id"] == run["thread_id"] for m in updates)
    # Retrying the same notification cannot overwrite or duplicate its original message.
    with home.store.connect() as db:
        home.store.message(db, "Changed result", identity=after[-1]["id"])
    assert home.store.conversation()["messages"] == after


def test_completed_task_recap_keeps_details_in_the_thread(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    summary = "The requested feature works and its tests pass.\n\n" + "Detailed findings. " * 100
    home.store.append_thread(
        run["thread_id"], {"id": "result", "role": "assistant", "body": summary}
    )
    assert home._finish(run["id"], "succeeded", summary, epoch=run["epoch"])
    home.deliver_outcomes()
    assert home.store.conversation()["messages"][-1]["body"] == summary.split("\n\n")[0]
    assert home.store.thread(run["thread_id"])["messages"][-1]["body"] == summary
    assert len(completion_recap("A long result " * 100)) <= 320


def test_reminder_final_card_retains_the_reminder_text(home: RemHome):
    def unexpected_generation(*args):
        raise AssertionError("A reminder should not call a provider")

    home.generate = unexpected_generation
    prompt = "Call Alex about the release tomorrow."
    task = home.create_task({"request_id": "reminder", **recipe(type="reminder", prompt=prompt)})[
        "task"
    ]
    run = wait_run(home, task["id"], {"succeeded"})
    message = home.store.conversation()["messages"][-1]
    assert message["body"] == prompt
    assert message["work"]["run_id"] == run["id"]
    assert message["work"]["state"] == "succeeded"


def test_verification_streams_large_files_and_detects_same_size_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    large = tmp_path / "archive.bin"
    with large.open("wb") as handle:
        handle.truncate(600 * 1024 * 1024)
    source = tmp_path / "source.py"
    source.write_text("value = 1\n")
    original_open = Path.open

    read_sizes = []

    class BoundedReader:
        def __init__(self, handle: Any) -> None:
            self.handle = handle

        def read(self, size: int) -> bytes:
            assert 0 < size <= 65536, "Verification must use bounded reads"
            read_sizes.append(size)
            return cast(bytes, self.handle.read(size))

    @contextmanager
    def bounded_open(path, *args, **kwargs):
        with original_open(path, *args, **kwargs) as handle:
            yield BoundedReader(handle) if path == large else handle

    monkeypatch.setattr(Path, "open", bounded_open)
    before = subject_hash(tmp_path)
    assert subject_hash(tmp_path) == before
    source.write_text("value = 2\n")
    changed = subject_hash(tmp_path)
    assert changed != before
    timestamp = large.stat().st_mtime_ns
    with original_open(large, "r+b") as edit_handle:
        edit_handle.seek(520 * 1024 * 1024)
        edit_handle.write(b"changed")
    os.utime(large, ns=(timestamp, timestamp))
    assert subject_hash(tmp_path) != changed
    assert sum(read_sizes) >= 4 * 600 * 1024 * 1024


def test_verification_ignores_dependencies_and_records_links_without_following_them(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("original")
    link = project / "external"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This filesystem does not allow symlink creation")
    before = subject_hash(project)
    (outside / "private.txt").write_text("changed")
    dependency = project / "node_modules"
    dependency.mkdir()
    (dependency / "cache.js").write_text("generated")
    assert subject_hash(project) == before
    link.unlink()
    assert subject_hash(project) != before


def test_verification_ignores_compiled_workflow_caches(tmp_path: Path):
    workflow = tmp_path / ".raticode" / "daily"
    compiled = workflow / "compiled"
    compiled.mkdir(parents=True)
    (workflow / "workflow.rattish").write_text("Rattish: 1\n")
    (compiled / "cache.json").write_text('{"workflow":"daily-4"}')
    unrelated = tmp_path / "src" / "compiled"
    unrelated.mkdir(parents=True)
    (unrelated / "output.js").write_text("one")
    before = subject_hash(tmp_path)
    # Another app instance recompiling the workflow is not a project change.
    (compiled / "cache.json").write_text('{"workflow":"daily-10"}')
    assert subject_hash(tmp_path) == before
    (unrelated / "output.js").write_text("two")
    assert subject_hash(tmp_path) != before
    before = subject_hash(tmp_path)
    (workflow / "workflow.rattish").write_text("Rattish: 1\n# edited\n")
    assert subject_hash(tmp_path) != before


def test_verification_has_no_file_count_cutoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    empty = tmp_path / "empty.txt"
    empty.touch()
    metadata = empty.lstat()
    original_stat = Path.lstat
    names = [f"source-{i:06}.txt" for i in range(100001)]
    monkeypatch.setattr(
        "gofer.ui.rem_home.os.walk", lambda *args, **kwargs: [(str(tmp_path), [], names)]
    )
    monkeypatch.setattr(
        Path, "lstat", lambda p: metadata if p.parent == tmp_path else original_stat(p)
    )
    before = subject_hash(tmp_path)
    names.append("new-source.txt")
    assert subject_hash(tmp_path) != before


def test_large_project_can_complete_review_but_reviewer_edits_still_block(
    home: RemHome, tmp_path: Path
):
    project = tmp_path / "project"
    project.mkdir()
    with (project / "archive.bin").open("wb") as handle:
        handle.truncate(600 * 1024 * 1024)
    source = project / "source.py"
    source.write_text("value = 1\n")
    task = home.create_task({"request_id": "large-project", **recipe(project)})["task"]
    assert wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=15)["state"] == "succeeded"
    original = home.source

    changes = []

    async def mutating_reviewer(**kwargs):
        if "independent read-only reviewer" in kwargs["agent_instructions"]:
            changes.append(kwargs)
            source.write_text(f"value = {len(changes) + 1}\n")
        async for event in original(**kwargs):
            yield event

    home.source = mutating_reviewer
    task = home.create_task({"request_id": "changed-project", **recipe(project)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=15)
    assert run["state"] == "blocked"
    assert "source files kept changing" in run["summary_short"].lower()
    assert len(changes) == 3


@pytest.mark.parametrize("problem", [False, True])
def test_background_tasks_proactively_post_updates_without_a_user_turn(
    home: RemHome, tmp_path: Path, problem: bool
):
    if problem:

        async def failed_source(**kwargs):
            raise ValueError("YouTube authorization is missing. Complete sign-in before retrying.")
            yield

        home.source = failed_source
    home.generate = lambda *args: {"summary": "Implemented and tested.", "needs_user": False}
    home.monitor.start()
    task = home.create_task({"request_id": "background", **recipe(tmp_path)})["task"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        run = home.store.tasks()[0]["last_run"]
        if run["state"] in {"succeeded", "blocked"} and not home.store.outbox():
            break
        time.sleep(0.01)
    else:
        pytest.fail("Background task did not finish")
    assert run["state"] == ("blocked" if problem else "succeeded")
    snapshot = home.store.conversation()
    assert snapshot["conversation"]["turn_state"] == "idle"
    assert not any(m["role"] == "user" for m in snapshot["messages"])
    update = snapshot["messages"][-1]
    assert update["kind"] == "task_update" and update["role"] == "rem"
    assert update["work"]["task_id"] == task["id"]
    assert update["work"]["state"] == run["state"]
    assert update["body"] == (
        "YouTube authorization is missing. Complete sign-in before retrying."
        if problem
        else "Implemented and tested."
    )
    assert any(
        e["kind"] == "conversation.message" and e["body"]["message_id"] == update["id"]
        for e in home.store.events(0)
    )
    assert not home._finish(run["id"], run["state"], "Repeated update", run["epoch"])
    assert home.store.conversation()["messages"] == snapshot["messages"]


def test_local_time_and_dst_are_not_elapsed_intervals():
    scheduled = Schedule(kind="daily", local_time="09:00", timezone="America/New_York")
    assert scheduled.next(datetime(2026, 3, 7, 15, tzinfo=UTC)) == "2026-03-08T13:00:00+00:00"
    assert (
        Schedule(kind="interval", every_minutes=60).next(datetime(2026, 3, 7, 15, tzinfo=UTC))
        == "2026-03-07T16:00:00+00:00"
    )
    fall = Schedule(kind="daily", local_time="01:30", timezone="America/New_York")
    assert fall.next(datetime(2026, 11, 1, 4, tzinfo=UTC)) == "2026-11-01T05:30:00+00:00"
    assert fall.next(datetime(2026, 11, 1, 5, 31, tzinfo=UTC)) == "2026-11-02T06:30:00+00:00"


def test_task_commands_are_idempotent_conflicts_preserve_data(home: RemHome, tmp_path: Path):
    payload = {"request_id": "create", **recipe(tmp_path)}
    created = home.create_task(payload)
    assert home.create_task(payload) == created
    assert len(home.store.tasks()) == 1
    with pytest.raises(RemConflict):
        home.create_task({**payload, "prompt": "Something else"})
    task = created["task"]
    with pytest.raises(RemConflict):
        home.edit_task(
            task["id"], {"request_id": "edit", "expected_revision": 0, "prompt": "New goal"}, "edit"
        )
    assert home.store.tasks()[0]["prompt"] == task["prompt"]


def test_worker_completion_is_independently_verified_and_linked(home: RemHome, tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    created = home.create_task({"request_id": "create", **recipe(project)})["task"]
    run = wait_run(home, created["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert run["verification_thread_id"] == run["thread_id"]
    assert run["criteria"][0]["result"] == "pass"
    assert any(
        m["body"] == "Implemented and tested."
        for m in home.store.thread(run["thread_id"])["messages"]
    )
    assert not home.store.thread(run["thread_id"])["running"]
    messages = home.store.conversation()["messages"]
    assert messages[0]["work"]["thread_id"] == run["thread_id"]
    assert messages[-1]["work"]["run_id"] == run["id"]
    for identity in (run["thread_id"], run["verification_thread_id"]):
        thread = home.store.thread(identity)
        # Finished task threads stay in the thread list; the desktop marks them unread.
        assert thread["metadata"]["archived"] is False
        assert "archivedAt" not in thread["metadata"]
        assert all("acceptance_criteria" not in m["body"] for m in thread["messages"])
    assert home.store.thread(run["thread_id"])["messages"][0]["body"] == created["prompt"]
    review_body = home.store.thread(run["verification_thread_id"])["messages"][-1]["body"]
    assert "Observed passing test receipt" in review_body
    assert '"criteria"' not in review_body
    review_jobs = list((home.jobs.directory / run["verification_thread_id"]).glob("*.jsonl"))
    assert review_jobs
    for path in review_jobs:
        events = [json.loads(line) for line in path.read_text().splitlines()]
        for event in events:
            if event.get("type") == "final":
                assert '"criteria"' not in event["message"]["body"]


@pytest.mark.parametrize("provider", CLI_PROVIDERS)
def test_rem_discussion_follows_up_in_one_thread_without_project_snapshot(
    home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
):
    def unexpected_snapshot(root):
        raise AssertionError("A discussion must not hash the whole project")

    monkeypatch.setattr("gofer.ui.rem_home.subject_hash", unexpected_snapshot)
    turns: list[dict[str, Any]] = []
    checks: list[str] = []
    question = "Give me a status update on where the Raticode project is at."
    follow_up = "Is there any work in progress?"
    task_recipe = recipe(
        tmp_path,
        type="errand",
        execution_mode="conversation",
        prompt=question,
        provider=provider,
        model="selected-model",
    )

    async def source(**kwargs):
        turns.append(kwargs)
        yield {
            "type": "thought",
            "text": "Reading the project history",
            "trace": {
                "id": "inspect",
                "category": "file_read",
                "phase": "result",
                "status": "completed",
                "output": "Feature shipped; no work in progress",
            },
        }
        yield {
            "type": "final",
            "message": {
                "body": "The feature shipped and tests passed."
                if len(turns) == 1
                else "No, everything is good."
            },
        }

    def generate(prompt, config, cancel):
        assert config["provider"] == "codex" and config["model"] == "rem-model"
        if "following up on a delegated conversation" not in prompt:
            assert "execution_mode:conversation" in prompt
            return {"reply": "I'll ask about Raticode.", "tasks": [task_recipe]}
        checks.append(prompt)
        return {
            "criteria": [
                {
                    "id": "check",
                    "result": "unknown" if len(checks) == 1 else "pass",
                    "evidence": "Need current work status"
                    if len(checks) == 1
                    else "Both questions answered using project records",
                }
            ],
            "follow_up": follow_up if len(checks) == 1 else "",
            "summary": "Raticode's feature is shipped, and there's no work in progress.",
        }

    home.source, home.generate = source, generate
    home.configure({"provider": "codex", "model": "rem-model", "effort": "high"})
    home.submit_message(
        {"request_id": "discuss", "body": "Start a thread discussing Raticode's current state."}
    )
    home._reply()
    task = home.store.tasks()[0]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert not run.get("verification_thread_id")
    assert len(turns) == len(checks) == 2
    assert len({o["conversation_id"] for o in turns}) == 1
    for options in turns:
        assert options["workflow"]["remManagedTask"]["readOnly"] is True
        assert options["provider"] == provider and options["model"] == "selected-model"
        assert "acceptance_criteria" not in options["messages"][-1]["body"]
    thread = home.store.thread(run["thread_id"])
    assert [m["body"] for m in thread["messages"] if m["role"] == "user"] == [question, follow_up]
    assert thread["metadata"]["archived"] is False
    assert not thread["running"]
    result = home.store.conversation()["messages"][-1]
    assert result["work"]["thread_id"] == run["thread_id"]
    assert result["body"] == "Raticode's feature is shipped, and there's no work in progress."


@pytest.mark.parametrize("provider", CLI_PROVIDERS)
def test_rem_answers_worker_questions_and_completes_requested_few_turns(
    home: RemHome, provider: str
):
    replies = [
        "My day is going well. Should I keep this casual or technical?",
        "Sounds good. What's been keeping you busy?",
        "That was a nice chat. Anything else you'd like to talk about?",
    ]
    messages = [
        "Let's keep it casual, as requested. What's been interesting today?",
        "I'm coordinating this conversation. What did you enjoy discussing?",
        "",
    ]
    turns, decisions = [], []

    async def source(**kwargs):
        turns.append(kwargs)
        assert "separate worker thread" in kwargs["agent_instructions"]
        assert "Rem is another agent, not the human user" in kwargs["agent_instructions"]
        assert kwargs["messages"][-1]["body"].startswith("Rem: ")
        yield {"type": "final", "message": {"body": replies[len(turns) - 1]}}

    def generate(prompt, config, cancel):
        if "following up on a delegated conversation" not in prompt:
            assert "For 'a few turns', use 3" in prompt
            return {
                "reply": "I'll chat with the thread and report back.",
                "tasks": [
                    recipe(
                        type="errand",
                        execution_mode="conversation",
                        conversation_turns=3,
                        prompt="How is your day going?",
                    )
                ],
            }
        decisions.append(prompt)
        assert "Answer the worker's ordinary questions" in prompt
        assert "reasonable, low-consequence defaults" in prompt
        assert "A friendly question at the end of a reply is NOT" in prompt
        assert '"speaker": "Rem"' in prompt and '"speaker": "worker"' in prompt
        assert '"speaker": "user"' not in prompt
        assert f'"completed_turns": {len(turns)}' in prompt
        return {
            "criteria": [{"id": "check", "result": "pass", "evidence": "Casual exchange observed"}],
            "next_message": messages[len(turns) - 1],
            "summary": "We chatted about the day and what we've been working on.",
            "needs_user": False,
            "user_question": "",
        }

    home.source, home.generate = source, generate
    home.configure({"provider": provider, "model": "selected-model"})
    home.submit_message(
        {"request_id": "chat", "body": "Chat with a thread for a few turns and report back."}
    )
    home._reply()
    task = home.store.tasks()[0]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert len(turns) == len(decisions) == 3
    assert not run["phase_label"]
    thread = home.store.thread(run["thread_id"])
    assert thread["metadata"]["archived"] is False
    assert [m["body"] for m in thread["messages"] if m["role"] == "user"] == [
        "How is your day going?",
        *messages[:2],
    ]
    assert all(m.get("sender") == "rem" for m in thread["messages"] if m["role"] == "user")
    assert home.store.conversation()["messages"][-1]["body"].startswith("We chatted")


def test_rem_repairs_empty_reply_instead_of_finishing_a_requested_chat_early(home: RemHome):
    replies, checks = [], []

    async def source(**kwargs):
        replies.append(kwargs["messages"][-1]["body"])
        yield {"type": "final", "message": {"body": "How are things going for you?"}}

    def generate(prompt, config, cancel):
        checks.append(prompt)
        return {
            "criteria": [{"id": "check", "result": "pass", "evidence": "Friendly chat"}],
            "next_message": "I'm coordinating this chat. What would you like to discuss?"
            if len(checks) == 2
            else "",
            "summary": "We had a brief chat.",
        }

    home.source, home.generate = source, generate
    task = home.create_task(
        {
            "request_id": "chat",
            **recipe(type="errand", execution_mode="conversation", conversation_turns=2),
        }
    )["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert len(replies) == 2 and len(checks) == 3
    assert "Supply your actual reply" in checks[1]


def test_rem_requests_attention_only_with_a_specific_consequential_question(home: RemHome):
    async def source(**kwargs):
        yield {"type": "final", "message": {"body": "May I pay for this service?"}}

    def generate(prompt, config, cancel):
        assert "Spending money, irreversible deletion" in prompt
        assert "require explicit user authorization" in prompt
        return {
            "criteria": [
                {"id": "check", "result": "unknown", "evidence": "Payment not authorized"}
            ],
            "next_message": "",
            "summary": "The worker needs purchase approval.",
            "needs_user": True,
            "user_question": "Do you authorize paying $20 for this service?",
        }

    home.source, home.generate = source, generate
    task = home.create_task(
        {"request_id": "chat", **recipe(type="errand", execution_mode="conversation")}
    )["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "blocked"
    assert run["summary_short"] == "Do you authorize paying $20 for this service?"
    assert not home.store.thread(run["thread_id"])["metadata"].get("archived")
    assert not run["phase_label"]


@pytest.mark.parametrize("needs_user", [False, True])
def test_rem_answers_work_clarifications_before_independent_review(
    home: RemHome,
    tmp_path: Path,
    needs_user: bool,
):
    original = home.source
    workers: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    question = (
        "May I pay for a testing service?" if needs_user else "Which test style should I use?"
    )

    async def source(**kwargs):
        is_review = "independent read-only reviewer" in kwargs["agent_instructions"]
        (reviews if is_review else workers).append(kwargs)
        if not is_review and len(workers) == 1:
            yield {"type": "final", "message": {"body": question}}
            return
        async for event in original(**kwargs):
            if is_review and len(reviews) == 1 and event["type"] == "final":
                event = {
                    "type": "final",
                    "message": {
                        "body": json.dumps(
                            {
                                "criteria": [
                                    {
                                        "id": "check",
                                        "result": "unknown",
                                        "evidence": "Worker is waiting for clarification",
                                    }
                                ],
                                "feedback": "Answer the worker's question, then finish the feature",
                                "question_to_rem": question,
                            }
                        )
                    },
                }
            yield event

    def generate(prompt, config, cancel):
        assert "Worker threads are separate agents" in prompt
        assert "independent review still checks completion" in prompt
        assert "require explicit user authorization" in prompt
        return {
            "next_message": "Use the project's existing test style and finish the feature."
            if not needs_user
            else "",
            "needs_user": needs_user,
            "user_question": "Do you authorize paying $20 for testing?" if needs_user else "",
        }

    home.source, home.generate = source, generate
    task = home.create_task({"request_id": "work", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == ("blocked" if needs_user else "succeeded"), run
    if needs_user:
        assert len(workers) == len(reviews) == 1
        assert run["summary_short"] == "Do you authorize paying $20 for testing?"
        assert not home.store.thread(run["thread_id"])["metadata"].get("archived")
    else:
        assert len(workers) == len(reviews) == 2
        assert "Use the project's existing test style" in workers[1]["messages"][-1]["body"]
        assert run["criteria"][0]["result"] == "pass"
        assert home.store.thread(run["thread_id"])["metadata"]["archived"] is False


def test_attention_decision_needs_a_real_question_instead_of_a_generic_pause(home: RemHome):
    home.generate = lambda p, c, e: {
        "criteria": [
            {"id": "check", "result": "unknown", "evidence": "Worker asked about Rem's day"}
        ],
        "next_message": "",
        "summary": "Awaiting the user's response",
        "needs_user": True,
    }
    task = home.create_task(
        {"request_id": "chat", **recipe(type="errand", execution_mode="conversation")}
    )["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "blocked"
    assert "validation error" not in run["summary_short"]
    assert "Awaiting the user's response" not in run["summary_short"]


@pytest.mark.parametrize("turns", [0, 13, 1.5, True])
def test_conversation_lengths_are_bounded_and_validated(turns: Any):
    with pytest.raises(ValueError):
        Recipe.model_validate(
            recipe(type="errand", execution_mode="conversation", conversation_turns=turns)
        )


def test_discussion_unknown_evidence_stays_visible_and_followups_are_bounded(
    home: RemHome, tmp_path: Path
):
    home.generate = lambda p, c, e: {
        "criteria": [
            {"id": "check", "result": "unknown", "evidence": "No project records inspected"}
        ],
        "follow_up": "What project records support that?",
        "summary": "Still missing evidence.",
    }
    task = home.create_task(
        {"request_id": "discuss", **recipe(tmp_path, type="errand", execution_mode="conversation")}
    )["task"]
    run = wait_run(home, task["id"], {"blocked", "succeeded"})
    assert run["state"] == "blocked"
    thread = home.store.thread(run["thread_id"])
    assert len([m for m in thread["messages"] if m["role"] == "user"]) == 3
    assert not thread["metadata"].get("archived")


def test_cancel_during_rem_discussion_check_cannot_complete_or_archive(
    home: RemHome, tmp_path: Path
):
    entered = threading.Event()

    def generate(prompt, config, cancel):
        entered.set()
        assert cancel.wait(3)
        return {
            "criteria": [{"id": "check", "result": "pass", "evidence": "Reply answered question"}],
            "follow_up": "",
            "summary": "All done.",
        }

    home.generate = generate
    task = home.create_task(
        {"request_id": "discuss", **recipe(tmp_path, type="errand", execution_mode="conversation")}
    )["task"]
    home.tick()
    assert entered.wait(2)
    run = home.store.tasks()[0]["active_run"]
    home.store.mutate_run(run["id"], {"request_id": "cancel"}, "cancel")
    run = wait_run(home, task["id"], {"cancelled"})
    assert not home.store.thread(run["thread_id"])["metadata"].get("archived")
    assert home.store.conversation()["messages"][-1]["body"] != "All done."


def test_edit_during_discussion_check_restarts_with_the_latest_question(
    home: RemHome, tmp_path: Path
):
    entered = threading.Event()
    checks = 0
    prompts = []

    async def source(**kwargs):
        prompts.append(kwargs["messages"][-1]["body"])
        yield {"type": "final", "message": {"body": "Current status from the records."}}

    def generate(prompt, config, cancel):
        nonlocal checks
        checks += 1
        if checks == 1:
            entered.set()
            assert cancel.wait(3)
        return {
            "criteria": [{"id": "check", "result": "pass", "evidence": "Question answered"}],
            "follow_up": "",
            "summary": "Old answer." if checks == 1 else "Latest question answered.",
        }

    home.source, home.generate = source, generate
    task = home.create_task(
        {
            "request_id": "discuss",
            **recipe(
                tmp_path, type="errand", execution_mode="conversation", prompt="How is the project?"
            ),
        }
    )["task"]
    home.tick()
    assert entered.wait(2)
    home.edit_task(
        task["id"],
        {"request_id": "edit", "expected_revision": 1, "prompt": "What is still unfinished?"},
        "edit",
    )
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert prompts == ["Rem: How is the project?", "Rem: What is still unfinished?"]
    assert home.store.thread(run["thread_id"])["metadata"]["archived"] is False
    assert not any(m["body"] == "Old answer." for m in home.store.conversation()["messages"])


def test_completion_keeps_review_attempts_listed_and_restart_restores_old_archives(
    home: RemHome, tmp_path: Path
):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    extra = "previous-review"
    with home.store.connect() as db:
        # Legacy installations can still contain separate review threads.
        metadata = home.store.get(db, "threads", run["thread_id"])
        home.store.put(db, "threads", {**metadata, "id": extra, "managedRole": "verify"})
    finished = wait_run(home, task["id"], {"succeeded"})
    assert not home.store.thread(extra)["metadata"].get("archived")
    assert home.store.thread(finished["thread_id"])["metadata"]["archived"] is False
    with home.store.connect() as db:
        # Earlier builds archived finished runs' threads and stamped archivedAt.
        for identity in (finished["thread_id"], extra):
            thread = home.store.get(db, "threads", identity)
            home.store.put(
                db, "threads", {**thread, "archived": True, "archivedAt": finished["finished_at"]}
            )
    restored = RemHomeStore(home.data_dir)
    for identity in (finished["thread_id"], extra):
        metadata = restored.thread(identity)["metadata"]
        assert metadata["archived"] is False
        assert "archivedAt" not in metadata
    assert restored.thread(finished["thread_id"])["messages"]


def test_old_task_without_execution_mode_remains_editable(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    with home.store.connect() as db:
        legacy = home.store.get(db, "tasks", task["id"])
        legacy.pop("execution_mode")
        home.store.put(db, "tasks", legacy)
    edited = home.edit_task(
        task["id"],
        {"request_id": "edit", "expected_revision": 1, "prompt": "Improve this feature"},
        "edit",
    )
    assert edited["task"]["execution_mode"] == "work"


def test_code_changes_cannot_skip_review_as_discussions(tmp_path: Path):
    with pytest.raises(ValueError, match="Only discussion tasks"):
        Recipe.model_validate(recipe(tmp_path, execution_mode="conversation"))


def test_missing_or_unknown_evidence_cannot_succeed(home: RemHome, tmp_path: Path):
    async def source(**kwargs):
        if "independent read-only reviewer" in kwargs["agent_instructions"]:
            answer = json.dumps(
                {
                    "criteria": [
                        {"id": "check", "result": "unknown", "evidence": "No test receipt"}
                    ],
                    "feedback": "Test it",
                }
            )
        else:
            answer = "Done. Trust me."
        yield {"type": "final", "message": {"body": answer}}

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"blocked", "succeeded"})
    assert run["state"] == "blocked"
    assert run["repair_count"] == 1
    assert "repeated the same unresolved result" in run["summary_short"]
    assert run["criteria"][0]["result"] == "unknown"


def test_optimistic_review_without_command_receipts_is_blocked(home: RemHome, tmp_path: Path):
    original = home.source

    async def source(**kwargs):
        async for event in original(**kwargs):
            if (
                event["type"] != "thought"
                or "independent read-only reviewer" in kwargs["agent_instructions"]
            ):
                yield event

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"blocked", "succeeded"})
    assert run["state"] == "blocked"
    assert run["repair_count"] == 1
    assert "command receipts" in run["summary_short"]
    assert all(c["result"] == "unknown" for c in run["criteria"])


def test_missing_worker_check_receipts_get_a_repair_turn(home: RemHome, tmp_path: Path):
    original = home.source
    workers: list[str] = []

    async def source(**kwargs):
        reviewer = "independent read-only reviewer" in kwargs["agent_instructions"]
        if not reviewer:
            workers.append(kwargs["messages"][-1]["body"])
        async for event in original(**kwargs):
            if reviewer or len(workers) > 1 or event["type"] != "thought":
                yield event

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"blocked", "succeeded"})
    assert run["state"] == "succeeded", run
    assert len(workers) == 2
    assert "command receipts" in workers[1]
    assert run["criteria"][0]["result"] == "pass"


def test_resumed_review_without_inspection_rechecks_in_a_fresh_session(
    home: RemHome, tmp_path: Path
):
    """A resumed reviewer can answer from memory; that must not fail passing work."""
    original = home.source
    reviews: list[dict[str, Any]] = []

    async def source(**kwargs):
        reviewer = "independent read-only reviewer" in kwargs["agent_instructions"]
        if reviewer:
            reviews.append(kwargs)
        async for event in original(**kwargs):
            if not (reviewer and len(reviews) == 1 and event["type"] == "thought"):
                yield event

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"blocked", "succeeded"})
    assert run["state"] == "succeeded", run
    # Each review of a new result starts fresh; the recheck does too, and its
    # transcript starts at its own request instead of replaying the old verdict.
    assert [r["reset_session"] for r in reviews] == [True, True]
    assert len(reviews[1]["messages"]) == 1
    assert run["repair_count"] == 0
    assert run["criteria"][0]["result"] == "pass"
    assert "earlier review are not evidence" in reviews[0]["agent_instructions"]


def test_review_after_repair_is_independent_of_earlier_verdicts(home: RemHome, tmp_path: Path):
    original = home.source
    reviews: list[dict[str, Any]] = []

    async def source(**kwargs):
        if "independent read-only reviewer" in kwargs["agent_instructions"]:
            reviews.append(kwargs)
            if len(reviews) == 1:
                yield {
                    "type": "thought",
                    "text": "Check",
                    "trace": {
                        "id": "first",
                        "category": "shell",
                        "phase": "result",
                        "status": "completed",
                        "exitCode": 0,
                        "output": "1 failed",
                    },
                }
                yield {
                    "type": "final",
                    "message": {
                        "body": json.dumps(
                            {
                                "criteria": [
                                    {"id": "check", "result": "fail", "evidence": "SECRET-1"}
                                ],
                                "feedback": "Fix it",
                            }
                        )
                    },
                }
                return
        async for event in original(**kwargs):
            yield event

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"blocked", "succeeded"})
    assert run["state"] == "succeeded", run
    assert [r["reset_session"] for r in reviews] == [True, True]
    assert not any("SECRET-1" in m["body"] for m in reviews[1]["messages"])
    assert not any("Fix it" in m["body"] for m in reviews[1]["messages"])


def test_review_that_never_inspects_blocks_without_claiming_passed_checks(
    home: RemHome, tmp_path: Path
):
    original = home.source
    workers: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []

    async def source(**kwargs):
        reviewer = "independent read-only reviewer" in kwargs["agent_instructions"]
        (reviews if reviewer else workers).append(kwargs)
        async for event in original(**kwargs):
            if not (reviewer and event["type"] == "thought"):
                yield event

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"blocked", "succeeded"})
    assert run["state"] == "blocked"
    assert len(workers) == 1 and len(reviews) == 2
    assert "without inspecting" in run["summary_short"]
    assert run["criteria"][0]["result"] == "unknown"
    assert "Observed passing test receipt" in run["criteria"][0]["evidence"]
    assert run["review_verdict"]["criteria"][0]["result"] == "pass"
    # The saved implementation can be reviewed again without repeating it.
    assert home.store.interrupted_review(run)


def test_review_retry_budgets_reset_for_each_worker_result(home: RemHome, tmp_path: Path):
    original = home.source
    reviews = 0
    target = tmp_path / "changing.txt"

    async def source(**kwargs):
        nonlocal reviews
        if "independent read-only reviewer" in kwargs["agent_instructions"]:
            reviews += 1
            if reviews in {1, 2, 4, 5}:
                # Four changes in total: two per worker result.
                target.write_text(str(reviews))
            if reviews == 3:
                yield {
                    "type": "thought",
                    "text": "Check",
                    "trace": {
                        "id": "fail",
                        "category": "shell",
                        "phase": "result",
                        "status": "completed",
                        "exitCode": 0,
                        "output": "1 failed",
                    },
                }
                yield {
                    "type": "final",
                    "message": {
                        "body": json.dumps(
                            {
                                "criteria": [
                                    {"id": "check", "result": "fail", "evidence": "1 failed"}
                                ],
                                "feedback": "Fix the failing test",
                            }
                        )
                    },
                }
                return
        async for event in original(**kwargs):
            yield event

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"blocked", "succeeded"})
    assert run["state"] == "succeeded", run
    assert run["repair_count"] == 1
    assert reviews == 6


def test_turn_limit_reports_a_clear_continuable_blocker(
    home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import gofer.ui.rem_home as rem_home_module

    monkeypatch.setattr(rem_home_module, "TURN_LIMIT_SECONDS", 0)

    async def source(**kwargs):
        while not kwargs["cancel_event"].is_set():
            await asyncio.sleep(0.01)
        yield {"type": "stopped"}

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"blocked", "succeeded", "outcome_unknown"})
    assert run["state"] == "blocked"
    assert "turn limit" in run["summary_short"]
    assert run["blocked_phase"] == "executing"


def test_followup_after_recovery_block_is_a_fresh_turn(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    with home.store.connect() as db:
        run.update(
            state="blocked",
            blocked_phase="executing",
            recovery_inspection=True,
            review_refresh_count=2,
            review_receipt_retry=True,
        )
        home.store.put(db, "runs", run)
    home.instruct(run["thread_id"], {"request_id": "follow", "body": "Please continue"})
    queued = home.store.tasks()[0]["active_run"]
    assert queued["state"] == "queued"
    assert not queued["recovery_inspection"]
    assert queued["review_refresh_count"] == 0
    assert not queued["review_receipt_retry"]
    finished = wait_run(home, task["id"], {"blocked", "succeeded"})
    assert finished["state"] == "succeeded", finished


def test_cancel_beats_late_worker_completion(home: RemHome, tmp_path: Path):
    entered = threading.Event()

    async def source(**kwargs):
        entered.set()
        while not kwargs["cancel_event"].is_set():
            await asyncio.sleep(0.01)
        yield {"type": "final", "message": {"body": "Done"}}

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    home.tick()
    assert entered.wait(2)
    run = home.store.tasks()[0]["active_run"]
    home.store.mutate_run(run["id"], {"request_id": "cancel"}, "cancel")
    finished = wait_run(home, task["id"], {"cancelled"})
    assert finished["state"] == "cancelled"
    assert not any(
        m.get("body", "").startswith("Finished Check")
        for m in home.store.conversation()["messages"]
    )


def test_edit_during_execution_drains_old_turn_and_updates_recipe(home: RemHome, tmp_path: Path):
    original = home.source
    entered = threading.Event()
    first = True

    async def source(**kwargs):
        nonlocal first
        if first:
            first = False
            entered.set()
            while not kwargs["cancel_event"].is_set():
                await asyncio.sleep(0.01)
            return
        async for event in original(**kwargs):
            yield event

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    home.tick()
    assert entered.wait(2)
    home.edit_task(
        task["id"], {"request_id": "edit", "expected_revision": 1, "prompt": "Updated goal"}, "edit"
    )
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert run["recipe"]["prompt"] == "Updated goal"
    assert run["epoch"] == 1
    assert any("Updated goal" in m["body"] for m in home.store.thread(run["thread_id"])["messages"])


def test_pause_does_not_cancel_current_and_delete_tombstones(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    paused = home.edit_task(task["id"], {"request_id": "pause", "cancel_current": False}, "pause")[
        "task"
    ]
    assert paused["state"] == "paused"
    assert paused["active_run"]["state"] == "queued"
    deleted = home.edit_task(task["id"], {"request_id": "delete"}, "delete")
    assert deleted["task"]["deleted_at"]
    assert deleted["task"]["active_run"]["state"] == "cancelling"
    assert not home.store.tasks()
    assert home.edit_task(task["id"], {"request_id": "delete"}, "delete") == deleted


def test_new_epoch_cannot_accept_old_review(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    home.edit_task(
        task["id"], {"request_id": "edit", "expected_revision": 1, "prompt": "Updated goal"}, "edit"
    )
    home._finish(run["id"], "succeeded", "Stale done", epoch=0)
    assert home.store.tasks()[0]["active_run"]["state"] == "queued"
    with pytest.raises(Superseded):
        home._update(run["id"], expected_epoch=0, state="verifying")


def test_schedules_coalesce_misfires_and_do_not_overlap(home: RemHome, tmp_path: Path):
    task = home.create_task(
        {
            "request_id": "create",
            **recipe(
                tmp_path, schedule={"kind": "daily", "local_time": "09:00", "timezone": "UTC"}
            ),
        }
    )["task"]
    later = datetime.now(UTC) + timedelta(days=20)
    home.store.due(later)
    home.store.due(later)
    with home.store.connect() as db:
        assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
    updated = home.store.tasks()[0]
    assert datetime.fromisoformat(updated["next_occurrence_at"]) > later
    assert updated["active_run"]["task_id"] == task["id"]


def test_repeated_runs_pick_up_after_the_last_accepted_run(home: RemHome, tmp_path: Path):
    prompts: list[str] = []
    source = home.source

    def recording(**kwargs: Any) -> Any:
        prompts.append(kwargs["agent_instructions"])
        return source(**kwargs)

    home.source = recording
    task = home.create_task(
        {
            "request_id": "create",
            **recipe(
                tmp_path, schedule={"kind": "daily", "local_time": "09:00", "timezone": "UTC"}
            ),
        }
    )["task"]
    first_at = task["next_occurrence_at"]
    clock = datetime.now(UTC) + timedelta(days=2)
    home.store.due(clock)
    first = wait_run(home, task["id"], {"succeeded"})
    assert first["scheduled_for"] == first_at
    assert first["previous_success"] is None
    worker, review = prompts
    context = json.loads(worker.split("Occurrence context:\n", 1)[1])
    assert context["this_run_for"] == first_at
    assert context["started_by"] == "schedule"
    assert "new_since" not in context
    assert "state the default you chose" in worker
    assert '"occurrence"' in review

    # A run that doesn't finish leaves the window open from the last accepted run.
    clock += timedelta(days=2)
    home.store.due(clock)
    second = home.store.tasks()[0]["active_run"]
    assert second["previous_success"]["run_id"] == first["id"]
    home._finish(second["id"], "blocked", "Couldn't reach the source.")
    clock += timedelta(days=2)
    home.store.due(clock)
    prompts.clear()
    third = wait_run(home, task["id"], {"succeeded"})
    assert third["id"] != second["id"]
    assert third["previous_success"] == {
        "run_id": first["id"],
        "for": first_at,
        "finished_at": first["finished_at"],
        "summary": "Implemented and tested.",
        "reports": [],
    }
    worker, review = prompts
    context = json.loads(worker.split("Occurrence context:\n", 1)[1])
    assert context["new_since"] == first_at
    assert context["this_run_for"] == third["scheduled_for"] > first_at
    assert context["previous_accepted_run"]["summary"] == "Implemented and tested."
    assert f'"new_since": "{first_at}"' in review


def test_one_off_work_gets_occurrence_context_only_after_a_success(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    first = wait_run(home, task["id"], {"succeeded"})
    assert occurrence_context(first) is None
    again = home.edit_task(task["id"], {"request_id": "again"}, "run")["run"]
    context = occurrence_context(again)
    assert context is not None
    assert context["started_by"] == "request"
    assert context["this_run_for"] == again["created_at"]
    assert context["new_since"] == first["created_at"]


def test_restart_never_replays_uncertain_write_and_recovers_conversation(
    home: RemHome, tmp_path: Path
):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    home._update(task["active_run"]["id"], state="executing")
    home.submit_message({"request_id": "message", "body": "Hi"})
    home._recover()
    recovered = home.store.tasks()[0]["last_run"]
    assert recovered["state"] == "queued"
    assert recovered["recovery_inspection"]
    assert recovered["resume_review"]
    updates = [m for m in home.store.conversation()["messages"] if m.get("kind") == "task_update"]
    assert len(updates) == 1
    assert updates[0]["work"]["state"] == "queued"
    assert "checking the saved thread" in updates[0]["body"]
    home._recover()
    assert [
        m for m in home.store.conversation()["messages"] if m.get("kind") == "task_update"
    ] == updates
    home._finish(task["active_run"]["id"], "succeeded", "Late completion", epoch=0)
    assert home.store.tasks()[0]["last_run"]["state"] == "queued"
    assert home.store.conversation()["conversation"]["turn_state"] == "replying"
    home._reply()
    assert home.store.conversation()["conversation"]["turn_state"] == "idle"
    assert not any("resend" in m["body"] for m in home.store.conversation()["messages"])


def test_conversation_is_durable_and_duplicate_delivery_does_not_launch_twice(home: RemHome):
    sent = {"request_id": "message", "body": "Hi"}
    receipt = home.submit_message(sent)
    assert home.submit_message(sent) == receipt
    home._reply()
    snapshot = home.store.conversation()
    assert [m["body"] for m in snapshot["messages"]] == ["Hi", "Hello."]
    reloaded = RemHomeStore(home.data_dir)
    assert reloaded.conversation() == snapshot
    home.configure({"provider": "claude_code", "model": "cli-default"})
    assert home.store.conversation()["conversation"]["id"] == snapshot["conversation"]["id"]


def test_conversation_delegates_and_keeps_recommended_criteria(home: RemHome, tmp_path: Path):
    home.generate = lambda p, c, e: {
        "reply": "I’ll handle it.",
        "tasks": [recipe(tmp_path, acceptance_criteria=[])],
        "memories": ["Prefers concise updates"],
    }
    home.submit_message({"request_id": "message", "body": "Implement this feature"})
    home._reply()
    task = home.store.tasks()[0]
    assert task["active_run"]["thread_id"]
    assert task["acceptance_criteria"][0]["authored_by"] == "rem_recommended"
    with home.store.connect() as db:
        assert home.store.setting(db, "memories")


def test_invalid_decision_cannot_execute_or_leave_conversation_stuck(home: RemHome):
    home.generate = lambda p, c, e: {"reply": "Hi", "execute_shell": "touch /tmp/bad"}
    home.submit_message({"request_id": "message", "body": "Hi"})
    home._reply()
    assert not home.store.tasks()
    assert home.store.conversation()["conversation"]["turn_state"] == "idle"
    error = home.store.conversation()["messages"][-1]["body"]
    assert "validation error" not in error and "pydantic" not in error
    assert "prepare the task" in error
    assert "try again" not in error and "resend" not in error


@pytest.mark.parametrize("provider", CLI_PROVIDERS)
def test_all_providers_execute_and_verify_using_selected_model(
    home: RemHome, tmp_path: Path, provider: str
):
    original = home.source
    observed = []

    async def source(**kwargs):
        observed.append(kwargs)
        async for event in original(**kwargs):
            yield event

    home.source = source
    effort = "" if provider == "opencode" else "high"
    home.configure({"provider": provider, "model": "selected-model", "effort": effort})
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert len(observed) == 2
    for options in observed:
        assert options["provider"] == provider
        assert options["model"] == "selected-model" and options["effort"] == (effort or None)
    assert observed[0]["workflow"]["remManagedTask"] == {"readOnly": False, "networkAccess": False}
    assert observed[1]["workflow"]["remManagedTask"] == {"readOnly": True, "networkAccess": False}
    assert observed[1]["workflow"]["remResources"]["web"] is False
    if provider in {"grok", "antigravity"}:
        assert all(o["permission_mode"] == "cli-managed" for o in observed)
    elif provider == "codex":
        assert observed[1]["permission_mode"] == "read-only"
    elif provider == "claude_code":
        assert all(o["permission_mode"] == "dontAsk" for o in observed)


@pytest.mark.parametrize("provider", ["codex", "claude_code"])
def test_only_codex_reviewers_get_a_private_scratch_folder(
    home: RemHome, tmp_path: Path, provider: str
):
    original = home.source
    observed: list[dict[str, Any]] = []

    async def source(**kwargs):
        observed.append(kwargs)
        scratch = kwargs.get("trusted_scratch_dir")
        if scratch:
            Path(scratch).mkdir(parents=True)
            (Path(scratch) / "cache").write_text("x")
        async for event in original(**kwargs):
            yield event

    home.source = source
    home.configure({"provider": provider})
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    assert wait_run(home, task["id"], {"succeeded", "blocked"})["state"] == "succeeded"
    worker, review = observed
    assert "trusted_scratch_dir" not in worker
    if provider == "claude_code":
        assert "trusted_scratch_dir" not in review
        return
    scratch = Path(review["trusted_scratch_dir"])
    assert scratch.is_relative_to(home.data_dir / "rem-work" / "review-scratch")
    assert review["permission_mode"] == "read-only"
    assert str(scratch) in review["agent_instructions"]
    assert not scratch.exists()


@pytest.mark.parametrize("schedule", [None, {}, {"kind": "once"}])
def test_conversation_null_schedule_launches_once_and_links_thread(
    home: RemHome, tmp_path: Path, schedule: Any
):
    home.generate = lambda p, c, e: {
        "reply": "I’ll start a thread to discuss the project.",
        "tasks": [recipe(tmp_path, type="errand", schedule=schedule)],
    }
    message = {"request_id": "discuss", "body": "Start a thread. Discuss where Raticode is."}
    home.submit_message(message)
    home._reply()
    assert home.submit_message(message)["accepted"]
    task = home.store.tasks()[0]
    assert task["schedule"]["kind"] == "once"
    assert task["next_occurrence_at"] is None
    run = task["active_run"]
    assert home.store.thread(run["thread_id"])["metadata"]["projectRoot"] == str(tmp_path)
    messages = home.store.conversation()["messages"]
    assert any(m.get("work", {}).get("thread_id") == run["thread_id"] for m in messages)
    assert not any("couldn’t finish" in m["body"] for m in messages)
    assert home.store.conversation()["conversation"]["turn_state"] == "idle"


@pytest.mark.parametrize("schedule", ["daily", 0, {"kind": "weekly", "weekdays": []}])
def test_schedule_default_does_not_hide_invalid_explicit_schedules(tmp_path: Path, schedule: Any):
    with pytest.raises(ValueError):
        Recipe.model_validate(recipe(tmp_path, schedule=schedule))


@pytest.mark.parametrize("provider", CLI_PROVIDERS)
def test_all_providers_research_save_and_review(home: RemHome, tmp_path: Path, provider: str):
    from tests.unit.test_swarm_tools import request

    original = home.source
    observed = []

    async def source(**kwargs):
        observed.append(kwargs)
        if "independent read-only reviewer" not in kwargs["agent_instructions"]:
            artifact = next(
                s
                for s in kwargs["workflow"]["remResources"]["mcpServers"]
                if s["name"] == "rem_artifacts"
            )
            response = request(
                artifact["url"],
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "save_report",
                        "arguments": {
                            "path": "findings.html",
                            "content": "<!doctype html><html><body>Verified findings</body></html>",
                        },
                    },
                },
            )[1]["result"]
            assert not response.get("isError"), response
        async for event in original(**kwargs):
            yield event

    home.source = source
    home.configure(
        {
            "provider": provider,
            "resources": {"web": True, "shell": True},
            "report_theme": {"format": "html", "theme": "blueprint"},
        }
    )
    task = home.create_task({"request_id": "research", **recipe(tmp_path, type="research")})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert run["artifacts"][0]["sha256"]
    assert len(observed) == 2
    assert all(o["workflow"]["remManagedTask"]["readOnly"] for o in observed)
    from gofer.ui.chat import build_chat_prompt
    from gofer.ui.report_outputs import with_report_outputs

    for options in observed:
        assert options["workflow"]["remReportTheme"] == {"format": "html", "theme": "blueprint"}
        prompt = build_chat_prompt(provider, "cli-default", [], options["workflow"])
        assert "Default report output: standalone HTML" in prompt
        assert "Blueprint:" in prompt
        assert "every informational file" in prompt
        assert with_report_outputs(options["workflow"], None, tmp_path) is options["workflow"]

    assert observed[0]["workflow"]["remResources"]["web"] is True
    assert observed[1]["workflow"]["remResources"]["web"] is False


def test_only_one_controller_can_admit_work(home: RemHome):
    assert home._lease()
    another = RemHome(
        home.data_dir,
        source=home.source,
        jobs=home.jobs,
        steering=home.steering,
        authorize=lambda c, r: None,
        start=False,
    )
    assert not another._lease()
    another.close()


def test_reviewer_must_cover_exact_current_criteria():
    item = Recipe.model_validate(recipe(Path("/project"))).model_dump()
    for values in [
        [],
        [{"id": "wrong", "result": "pass", "evidence": "Test"}],
        [{"id": "check", "result": "pass", "evidence": ""}],
        [{"id": "check", "result": "pass", "evidence": "Test"}] * 2,
    ]:
        with pytest.raises(ValueError):
            RemHome._verdict(json.dumps({"criteria": values}), item)


def test_reviewer_placeholder_criterion_does_not_discard_the_review():
    item = Recipe.model_validate(recipe(Path("/project"))).model_dump()
    verdict = RemHome._verdict(
        json.dumps(
            {
                "criteria": [
                    {"id": "check", "result": "fail", "evidence": "Root cause unconfirmed"},
                    {
                        "id": "wait_placeholder_unused",
                        "result": "unknown",
                        "evidence": "Not a task criterion; ignore.",
                    },
                ]
            }
        ),
        item,
    )
    assert verdict["criteria"] == [
        {"id": "check", "result": "fail", "evidence": "Root cause unconfirmed"}
    ]
    assert "Root cause unconfirmed" in RemHome._review_summary(json.dumps(verdict), item)


@pytest.mark.parametrize("readable_retry", [True, False])
def test_unreadable_review_is_retried_once_in_a_fresh_session(
    home: RemHome, tmp_path: Path, readable_retry: bool
):
    original = home.source
    reviews: list[dict[str, Any]] = []

    async def source(**kwargs):
        if "independent read-only reviewer" in kwargs["agent_instructions"]:
            reviews.append(kwargs)
            if len(reviews) == 1 or not readable_retry:
                yield {"type": "final", "message": {"body": json.dumps({"criteria": []})}}
                return
        async for event in original(**kwargs):
            yield event

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"blocked", "succeeded"})
    assert len(reviews) == 2
    assert run["review_format_retry"] is True
    if readable_retry:
        assert run["state"] == "succeeded", run
    else:
        assert run["state"] == "blocked"
        assert "The work is saved" in run["summary_short"]
        assert "exactly once" in run["error_detail"]


def test_thread_followup_invalidates_evidence_and_is_idempotent(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    original = task["active_run"]
    request = {"request_id": "instruction", "body": "Also cover keyboard navigation"}
    updated = home.instruct(original["thread_id"], request)
    assert home.instruct(original["thread_id"], request) == updated
    assert updated["run"]["epoch"] == 1
    assert "keyboard navigation" in updated["run"]["pending_recipe"]["prompt"]
    assert updated["run"]["criteria"][0]["result"] == "unknown"
    assert home.store.thread(original["thread_id"])["messages"][0]["body"] == request["body"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded"
    followup = home.instruct(run["thread_id"], {"request_id": "again", "body": "Add a second case"})
    assert followup["run"]["id"] != run["id"]
    assert followup["run"]["thread_id"] == run["thread_id"]
    assert followup["run"]["state"] == "queued"


def test_conversation_controls_existing_task_instead_of_duplicating(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    home.generate = lambda p, c, e: {
        "reply": "Paused.",
        "controls": [{"task_id": task["id"], "action": "pause"}],
    }
    home.submit_message({"request_id": "pause-message", "body": "Pause that task"})
    home._reply()
    assert len(home.store.tasks()) == 1
    assert home.store.tasks()[0]["state"] == "paused"
    assert home.store.tasks()[0]["active_run"]["state"] == "queued"


def test_access_revocation_drains_worker_and_blocks_completion(home: RemHome, tmp_path: Path):
    entered = threading.Event()
    original = home.source

    async def source(**kwargs):
        entered.set()
        while not kwargs["cancel_event"].is_set():
            await asyncio.sleep(0.01)
        async for event in original(**kwargs):
            yield event

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    home.tick()
    assert entered.wait(2)

    def revoked(c, r):
        raise ValueError("Project access was revoked")

    home.authorize = revoked
    run = wait_run(home, task["id"], {"blocked", "succeeded"})
    assert run["state"] == "blocked"
    assert "revoked" in run["summary_short"]


def test_same_project_workers_are_serialized(home: RemHome, tmp_path: Path):
    entered = threading.Event()
    release = threading.Event()
    original = home.source

    async def source(**kwargs):
        if "independent read-only reviewer" not in kwargs["agent_instructions"]:
            entered.set()
            while not release.is_set() and not kwargs["cancel_event"].is_set():
                await asyncio.sleep(0.01)
        async for event in original(**kwargs):
            yield event

    home.source = source
    first = home.create_task({"request_id": "first", **recipe(tmp_path)})["task"]
    second = home.create_task({"request_id": "second", **recipe(tmp_path)})["task"]
    try:
        home.tick()
        assert entered.wait(2)
        home.tick()
        assert len(home.workers) == 1
        waiting = next(t for t in home.store.tasks() if t["id"] == second["id"])["active_run"]
        assert waiting["state"] == "queued"
        assert waiting["waiting_reason"] == "Waiting for another task in this project to finish"
    finally:
        release.set()
    assert wait_run(home, first["id"], {"succeeded", "blocked"})["state"] == "succeeded"
    finished = wait_run(home, second["id"], {"succeeded", "blocked"})
    assert finished["state"] == "succeeded"
    assert finished["waiting_reason"] is None


def test_read_only_research_does_not_wait_for_same_project_work(home: RemHome, tmp_path: Path):
    entered: list[str] = []
    release = threading.Event()
    original = home.source

    async def source(**kwargs):
        if "independent read-only reviewer" not in kwargs["agent_instructions"]:
            entered.append(kwargs["workflow"]["chatThreadId"])
            while not release.is_set() and not kwargs["cancel_event"].is_set():
                await asyncio.sleep(0.01)
        async for event in original(**kwargs):
            yield event

    home.source = source
    writer = home.create_task({"request_id": "writer", **recipe(tmp_path)})["task"]
    first = home.create_task({"request_id": "r1", **recipe(tmp_path, type="research")})["task"]
    second = home.create_task({"request_id": "r2", **recipe(tmp_path, type="research")})["task"]
    try:
        home.tick()
        home.tick()
        assert len(home.workers) == 3
        deadline = time.monotonic() + 2
        while len(entered) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(entered) == 3
        for task in home.store.tasks():
            assert not (task.get("active_run") or {}).get("waiting_reason")
    finally:
        release.set()
    for task in (writer, first, second):
        wait_run(home, task["id"], {"succeeded", "blocked"})


def git(cwd: Path, *args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_concurrent_project_edits_use_a_temporary_worktree(
    home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project = tmp_path / "repo"
    project.mkdir()
    git(project, "init", "-q", "-b", "main")
    (project / "README.md").write_text("hello\n")
    git(project, "add", "-A")
    git(project, "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-qm", "init")
    roots: list[str] = []
    release = threading.Event()
    original = home.source

    async def source(**kwargs):
        if "independent read-only reviewer" not in kwargs["agent_instructions"]:
            root = Path(kwargs["workflow"]["projectRoot"])
            roots.append(str(root))
            if root != project:
                assert "temporary git worktree" in kwargs["agent_instructions"]
                (root / "feature.txt").write_text("isolated change\n")
            while not release.is_set() and not kwargs["cancel_event"].is_set():
                await asyncio.sleep(0.01)
        async for event in original(**kwargs):
            yield event

    home.source = source
    first = home.create_task({"request_id": "first", **recipe(project)})["task"]
    second = home.create_task({"request_id": "second", **recipe(project)})["task"]
    for key in ("GIT_AUTHOR", "GIT_COMMITTER"):
        monkeypatch.setenv(f"{key}_NAME", "Rem")
        monkeypatch.setenv(f"{key}_EMAIL", "rem@example.com")
    try:
        home.tick()
        home.tick()
        deadline = time.monotonic() + 5
        while len(roots) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(roots) == 2, roots
        assert str(project) in roots
        waiting = next(t for t in home.store.tasks() if t["id"] == second["id"])["active_run"]
        assert not waiting.get("waiting_reason")
    finally:
        release.set()
    for task in (first, second):
        assert wait_run(home, task["id"], {"succeeded", "blocked"})["state"] == "succeeded"
    finished = wait_run(home, second["id"], {"succeeded"})
    assert not finished["worktree"]
    assert finished["worktree_note"].startswith("Another task was changing")
    isolated = next(r for r in roots if r != str(project))
    assert not Path(isolated).exists()
    branch = next(line.strip() for line in git(project, "branch").splitlines() if "rem/" in line)
    assert branch in finished["worktree_note"]
    assert git(project, "show", f"{branch}:feature.txt") == "isolated change"
    assert not (project / "feature.txt").exists()
    assert git(project, "worktree", "list").count("\n") == 0


def test_rem_messages_carry_attachments_to_the_decision(home: RemHome, tmp_path: Path):
    import base64

    from gofer.ui.chat_media import store_chat_attachments

    stored = store_chat_attachments(
        {
            "threadId": "rem-home",
            "files": [
                {
                    "name": "notes.txt",
                    "type": "text/plain",
                    "data": base64.b64encode(b"long pasted reference").decode(),
                }
            ],
        },
        home.data_dir,
    )["attachments"]
    prompts: list[str] = []

    def generate(prompt: str, config: dict[str, Any], cancel: threading.Event) -> dict[str, Any]:
        prompts.append(prompt)
        return {"reply": "Got it."}

    home.generate = generate
    home.submit_message({"request_id": "with-file", "body": "", "attachments": stored})
    message = home.store.conversation()["messages"][0]
    assert message["attachments"][0]["name"] == "notes.txt"
    assert Path(message["attachments"][0]["path"]).read_text() == "long pasted reference"
    home._reply()
    assert "long pasted reference" in prompts[0]
    with pytest.raises(ValueError):
        home.submit_message(
            {
                "request_id": "forged",
                "body": "x",
                "attachments": [{"storageName": "0" * 32 + "-missing.txt", "name": "x"}],
            }
        )


def test_rem_replies_attach_requested_files_from_open_folders(home: RemHome, tmp_path: Path):
    project, brain, outside = tmp_path / "project", tmp_path / "brain", tmp_path / "outside"
    for folder in (project, brain / "reports", outside):
        folder.mkdir(parents=True)
    report = brain / "reports" / "audit.html"
    report.write_text("<p>Audit</p>")
    (outside / "secret.txt").write_text("no")
    (project / "link.txt").symlink_to(outside / "secret.txt")
    home.configure(
        {
            "provider": "codex",
            "model": "cli-default",
            "projects": [{"root": str(project), "name": "Project"}],
            "brain": {"root": str(brain), "enabled": True},
        }
    )
    named = [str(report), str(outside / "secret.txt"), str(project / "link.txt"), "relative.md"]
    home.generate = lambda p, c, e: {"reply": "Here's the audit.", "files": named}
    home.submit_message({"request_id": "send-report", "body": "Send me the audit report"})
    home._reply()
    reply = home.store.conversation()["messages"][-1]
    assert reply["id"] == "reply-send-report" and reply["reply_to"] == "send-report"
    assert reply["files"] == [{"name": "audit.html", "size": 12, "path": str(report.resolve())}]
    # Outside the open folders, a path Rem wasn't given counts as a guess.
    assert "secret.txt: its path isn’t in our conversation" in reply["body"]
    assert "link.txt: that file doesn’t exist" in reply["body"]

    # A reply that starts a task carries its files in a separate message.
    home.generate = lambda p, c, e: {
        "reply": "Starting.",
        "tasks": [recipe(project)],
        "files": [str(report)],
    }
    home.submit_message({"request_id": "with-task", "body": "Fix it and send the audit"})
    home._reply()
    files = next(m for m in home.store.conversation()["messages"] if m["id"] == "files-with-task")
    assert files["files"][0]["name"] == "audit.html" and files["reply_to"] == "with-task"


def test_research_output_service_proves_destination_and_hash(home: RemHome, tmp_path: Path):
    from gofer.ui.rem_home_artifacts import artifact_tools, validate_artifacts
    from tests.unit.test_swarm_tools import request

    task = home.create_task({"request_id": "create", **recipe(tmp_path, type="research")})["task"]
    run = task["active_run"]
    document = (
        "<!doctype html><html><head><title>Research</title></head><body>Findings</body></html>"
    )
    run["config"]["report_theme"] = {"format": "html", "theme": "blueprint"}
    with artifact_tools(home, run, "execute") as config:
        assert config is not None
        tools = request(config["url"], {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})[1][
            "result"
        ]["tools"]
        assert "Blueprint:" in tools[0]["description"]
        assert "every informational file" in tools[0]["description"]
        assert "Default report output: standalone HTML" in tools[0]["description"]

        def save(path: str) -> dict[str, Any]:
            return cast(
                dict[str, Any],
                request(
                    config["url"],
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "save_report",
                            "arguments": {"path": path, "content": document},
                        },
                    },
                )[1]["result"],
            )

        assert save("../../escaped.html")["isError"] is True
        assert save("research.md")["isError"] is True
        response = save("research.html")
        assert not response.get("isError"), response
        with home.store.connect() as db:
            saved = home.store.get(db, "runs", run["id"])
        assert Path(saved["artifacts"][0]["path"]).is_relative_to(tmp_path / "reports" / "rem")
        validate_artifacts(saved)
        home._update(run["id"], epoch=1)
        assert save("stale.html")["isError"] is True
        saved["epoch"] = 1
        with pytest.raises(ValueError, match="No saved research"):
            validate_artifacts(saved)
        saved["epoch"] = 0
        Path(saved["artifacts"][0]["path"]).write_text(document + "tampered")
        with pytest.raises(ValueError, match="changed"):
            validate_artifacts(saved)


def test_research_without_saved_artifact_cannot_be_accepted(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path, type="research")})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "blocked"
    # The identical unsaved result after one follow-up stops the loop early.
    assert run["repair_count"] == 1
    assert "repeated the same result" in run["summary_short"]
    assert "No saved research" in run["summary_short"]


def test_research_save_refused_by_provider_blocks_without_repeating(home: RemHome, tmp_path: Path):
    workers = 0

    async def source(**kwargs):
        nonlocal workers
        workers += 1
        yield {
            "type": "thought",
            "trace": {
                "id": "save-1",
                "kind": "tool",
                "title": "mcp__rem_artifacts__save_report",
                "phase": "start",
                "status": "running",
            },
        }
        yield {
            "type": "thought",
            "trace": {
                "id": "save-1",
                "kind": "tool",
                "title": "Tool result",
                "phase": "result",
                "status": "error",
                "output": "Cannot call mcp__rem_artifacts__save_report while in plan mode.",
            },
        }
        yield {"type": "final", "message": {"body": f"Draft ready but unsaved {workers}."}}

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path, type="research")})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "blocked"
    assert workers == 1 and run["repair_count"] == 0
    assert "refused the save tool" in run["summary_short"]
    assert "plan mode" in run["summary_short"]


def test_save_denial_ignores_successful_saves_and_unrelated_errors():
    from gofer.ui.rem_home import save_denial

    assert save_denial([]) is None
    assert (
        save_denial(
            [
                {
                    "id": "a",
                    "title": "Bash",
                    "phase": "result",
                    "status": "error",
                    "output": "permission denied",
                },
                {
                    "id": "b",
                    "title": "save_report",
                    "phase": "result",
                    "status": "completed",
                    "output": "saved",
                },
            ]
        )
        is None
    )
    assert (
        save_denial(
            [
                {
                    "id": "c",
                    "title": "rem_artifacts.save_report",
                    "phase": "result",
                    "status": "failed",
                    "error": "Tool use was denied by the user's permission mode",
                }
            ]
        )
        == "Tool use was denied by the user's permission mode"
    )


def save_through_rem_artifacts(
    kwargs: dict[str, Any], path: str, call_id: str
) -> list[dict[str, Any]]:
    """Call the granted save tool over MCP and return the provider traces it would show."""
    from tests.unit.test_swarm_tools import request

    artifact = next(
        s for s in kwargs["workflow"]["remResources"]["mcpServers"] if s["name"] == "rem_artifacts"
    )
    result = request(
        artifact["url"],
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "save_report",
                "arguments": {
                    "path": path,
                    "content": "<!doctype html><html><body>Findings</body></html>",
                },
            },
        },
    )[1]["result"]
    trace = {"id": call_id, "kind": "tool"}
    return [
        {**trace, "title": "mcp__rem_artifacts__save_report", "phase": "start"},
        {
            **trace,
            "title": "Tool result",
            "phase": "result",
            "status": "error" if result.get("isError") else "completed",
            "output": result["content"][0]["text"],
        },
    ]


def test_report_name_clash_is_explained_and_recovers_with_swarms_off(home: RemHome, tmp_path: Path):
    """The work-machine incident: an earlier attempt's report blocked every save."""
    original = home.source
    outputs: list[str] = []
    observed: list[dict[str, Any]] = []
    earlier = tmp_path / "reports" / "rem" / "findings.html"
    earlier.parent.mkdir(parents=True)
    earlier.write_text("earlier attempt")

    async def source(**kwargs):
        if "independent read-only reviewer" not in kwargs["agent_instructions"]:
            observed.append(kwargs)
            for trace in save_through_rem_artifacts(kwargs, "findings.html", "save-1"):
                yield {"type": "thought", "trace": trace}
            outputs.append(trace["output"])
            for trace in save_through_rem_artifacts(kwargs, "findings-v2.html", "save-2"):
                yield {"type": "thought", "trace": trace}
            outputs.append(trace["output"])
        async for event in original(**kwargs):
            yield event

    home.source = source
    home.configure({"provider": "codex", "swarm_access": False})
    task = home.create_task({"request_id": "research", **recipe(tmp_path, type="research")})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    # Swarms are off for the thread, and the report tool is still granted.
    assert observed[0]["workflow"]["remSwarmAccess"]["enabled"] is False
    assert "Swarm" not in outputs[0] and "orchestrator" not in outputs[0]
    assert "already exists at findings.html" in outputs[0] and "findings-v2.html" in outputs[0]
    assert earlier.read_text() == "earlier attempt"
    assert [Path(a["path"]).name for a in run["artifacts"]] == ["findings-v2.html"]
    instructions = observed[0]["agent_instructions"]
    assert "Saved reports are never replaced" in instructions


def test_identical_save_error_after_follow_up_stops_with_the_cause(home: RemHome, tmp_path: Path):
    workers: list[str] = []
    earlier = tmp_path / "reports" / "rem" / "findings.html"
    earlier.parent.mkdir(parents=True)
    earlier.write_text("earlier attempt")

    async def source(**kwargs):
        workers.append(kwargs["messages"][-1]["body"])
        for trace in save_through_rem_artifacts(kwargs, "findings.html", f"save-{len(workers)}"):
            yield {"type": "thought", "trace": trace}
        # A different answer each turn, so only the repeated tool error can stop it.
        yield {"type": "final", "message": {"body": f"Draft {len(workers)} is unsaved."}}

    home.source = source
    task = home.create_task({"request_id": "research", **recipe(tmp_path, type="research")})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "blocked"
    assert len(workers) == 2 and run["repair_count"] == 1
    # The follow-up carried the tool's own explanation, not a bare "save again".
    assert "The last save_report call returned" in workers[1]
    assert "findings-v2.html" in workers[1]
    assert "failed the same way again" in run["summary_short"]
    assert "already exists" in run["summary_short"]


def test_receipt_failure_after_writing_says_so_and_a_new_name_recovers(
    home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import sqlite3

    from gofer.ui.rem_home_artifacts import artifact_tools, validate_artifacts
    from tests.unit.test_swarm_tools import request

    task = home.create_task({"request_id": "create", **recipe(tmp_path, type="research")})["task"]
    run = task["active_run"]
    connect = home.store.connect
    calls = {"count": 0, "fail_at": 2}

    def flaky_connect(*args: Any, **kwargs: Any):
        calls["count"] += 1
        if calls["count"] == calls["fail_at"]:
            raise sqlite3.OperationalError("database is locked")
        return connect(*args, **kwargs)

    with artifact_tools(home, run, "execute") as config:
        assert config is not None

        def save(path: str) -> dict[str, Any]:
            return cast(
                dict[str, Any],
                request(
                    config["url"],
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "save_report",
                            "arguments": {
                                "path": path,
                                "content": "<!doctype html><html><body>F</body></html>",
                            },
                        },
                    },
                )[1]["result"],
            )

        monkeypatch.setattr(home.store, "connect", flaky_connect)
        failed = save("research.html")
        assert failed["isError"] is True
        text = failed["content"][0]["text"]
        assert "written to research.html" in text
        assert "receipt couldn't be recorded (OperationalError)" in text
        assert "new name" in text and str(tmp_path) not in text
        assert (tmp_path / "reports" / "rem" / "research.html").exists()
        calls.update(count=0, fail_at=0)
        assert not save("research-v2.html").get("isError")
    monkeypatch.setattr(home.store, "connect", connect)
    with home.store.connect() as db:
        saved = home.store.get(db, "runs", run["id"])
    assert [Path(a["path"]).name for a in saved["artifacts"]] == ["research-v2.html"]
    validate_artifacts(saved)


def test_own_tool_failures_are_not_provider_refusals():
    from gofer.ui.rem_home import save_denial, save_errors

    traces = [
        {"id": "s", "title": "mcp__rem_artifacts__save_report", "phase": "start"},
        {
            "id": "s",
            "title": "Tool result",
            "phase": "result",
            "status": "error",
            "output": "Report tool save_report failed (PermissionError).\n Try once more.",
        },
    ]
    assert save_denial(traces) is None
    assert list(save_errors(traces)) == [
        "Report tool save_report failed (PermissionError). Try once more."
    ]


def test_only_research_workers_are_told_to_use_rem_artifacts(home: RemHome, tmp_path: Path):
    original = home.source
    instructions: list[str] = []

    async def source(**kwargs):
        if "independent read-only reviewer" not in kwargs["agent_instructions"]:
            instructions.append(kwargs["agent_instructions"])
        async for event in original(**kwargs):
            yield event

    home.source = source
    task = home.create_task({"request_id": "errand", **recipe(tmp_path, type="errand")})["task"]
    wait_run(home, task["id"], {"succeeded", "blocked"})
    assert "This task has no rem_artifacts tool" in instructions[0]
    assert "using rem_artifacts.save_report" not in instructions[0]


def test_outcome_update_drops_promises_rem_cannot_keep():
    from gofer.ui.rem_home import drop_unbacked_promises

    text = (
        "The report is drafted but not saved. The worker's provider refused the save tool. "
        "Next, I'll switch that thread out of plan mode. Use Run again to retry."
    )
    assert drop_unbacked_promises(text) == (
        "The report is drafted but not saved. The worker's provider refused the save tool. "
        "Use Run again to retry."
    )
    assert drop_unbacked_promises("I will fix it.") == ""


def test_research_missing_report_gets_a_repair_turn_before_review(home: RemHome, tmp_path: Path):
    from tests.unit.test_swarm_tools import request

    original = home.source
    workers: list[str] = []
    reviews = 0

    async def source(**kwargs):
        nonlocal reviews
        if "independent read-only reviewer" in kwargs["agent_instructions"]:
            reviews += 1
        else:
            workers.append(kwargs["messages"][-1]["body"])
            if len(workers) == 2:
                artifact = next(
                    s
                    for s in kwargs["workflow"]["remResources"]["mcpServers"]
                    if s["name"] == "rem_artifacts"
                )
                request(
                    artifact["url"],
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "save_report",
                            "arguments": {
                                "path": "findings.html",
                                "content": "<!doctype html><html><body>Findings</body></html>",
                            },
                        },
                    },
                )
        async for event in original(**kwargs):
            yield event

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path, type="research")})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert len(workers) == 2 and reviews == 1
    assert "rem_artifacts.save_report" in workers[1]


def test_follow_up_keeps_the_report_saved_before_it(home: RemHome, tmp_path: Path):
    from tests.unit.test_swarm_tools import request

    original = home.source
    workers: list[str] = []
    reviews = 0

    async def source(**kwargs):
        nonlocal reviews
        if "independent read-only reviewer" in kwargs["agent_instructions"]:
            reviews += 1
            if reviews == 1:
                # The first review never starts, which blocks the run.
                yield {"type": "error", "error": "Could not start 'claude' CLI"}
                return
        else:
            workers.append(kwargs["messages"][-1]["body"])
            if len(workers) == 1:
                artifact = next(
                    s
                    for s in kwargs["workflow"]["remResources"]["mcpServers"]
                    if s["name"] == "rem_artifacts"
                )
                request(
                    artifact["url"],
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "save_report",
                            "arguments": {
                                "path": "findings.html",
                                "content": "<!doctype html><html><body>Findings</body></html>",
                            },
                        },
                    },
                )
        async for event in original(**kwargs):
            yield event

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path, type="research")})["task"]
    blocked = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert blocked["state"] == "blocked", blocked
    home.instruct(blocked["thread_id"], {"request_id": "continue", "body": "continue"})
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    # The follow-up is reviewed against the report it already has instead of
    # being sent back to save that report again.
    assert len(workers) == 2 and reviews == 2
    assert not any("Please address these findings" in body for body in workers)
    assert [a["epoch"] for a in run["artifacts"]] == [run["epoch"]]


def test_requeued_runs_keep_saved_reports_but_not_superseded_ones():
    from gofer.ui.rem_home_store import begin_cycle

    run = {
        "epoch": 2,
        "config": {},
        "recipe": {"acceptance_criteria": []},
        "artifacts": [
            {"path": "/reports/saved.html", "epoch": 0},
            {"path": "/reports/late.html", "epoch": 1, "superseded": True},
        ],
    }
    begin_cycle(run, "work")
    assert [a["epoch"] for a in run["artifacts"]] == [3, 1]
    begin_cycle(run, "review")
    assert [a["epoch"] for a in run["artifacts"]] == [4, 1]
    begin_cycle(run, "recover")
    assert [a["epoch"] for a in run["artifacts"]] == [5, 1]


def test_startup_waits_for_current_context_before_admitting_queued_work(
    home: RemHome, tmp_path: Path
):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    home.has_context = False
    home.tick()
    assert not home.workers
    assert home.store.tasks()[0]["active_run"]["state"] == "queued"
    home.configure({"provider": "codex", "projects": []})
    assert wait_run(home, task["id"], {"succeeded", "blocked"})["state"] == "succeeded"


def test_rem_rebuilds_youtube_goal_and_passes_old_report_to_standalone_worker(home: RemHome):
    from tests.unit.test_rem_home_recall import linked_run

    with home.store.connect() as db:
        home.store.message(
            db,
            "Fetch YouTube transcripts from subscribed AI channels, daily at 6AM. "
            "Save the report in my Second Brain.",
            role="user",
            identity="youtube-goal",
        )
    previous = linked_run(home.store)
    for i in range(26):
        home.store.append_thread(
            previous["thread_id"],
            {
                "id": f"old-{i}",
                "role": "assistant",
                "body": "The report recommends captions-service-v2. /reports/youtube.html"
                if i == 0
                else "Discussed implementation details.",
            },
        )
    with home.store.connect() as db:
        for i in range(80):
            home.store.message(db, f"Unrelated chat {i}")
    observed = []
    original_source = home.source

    async def source(**kwargs):
        observed.append(kwargs)
        async for event in original_source(**kwargs):
            yield event

    calls = []
    goal = "Build the standalone YouTube script using the report, with daily reports at 6AM "
    goal += "saved to Second Brain. Keep it outside Raticode."

    def generate(prompt, config, cancel):
        calls.append(prompt)
        if len(calls) == 1:
            assert "youtube-goal" in prompt and "6AM" in prompt
            assert previous["thread_id"] in prompt
            assert "captions-service-v2" not in prompt
            return {
                "recall": {
                    "threads": [{"thread_id": previous["thread_id"], "before_message_id": "old-6"}]
                }
            }
        assert "captions-service-v2" in prompt
        assert "No further recall" not in prompt
        assert not any(t["title"] == "Daily YouTube script" for t in home.store.tasks())
        return {
            "reply": "I'll build the standalone script using our earlier report.",
            "tasks": [recipe(title="Daily YouTube script", prompt=goal)],
        }

    home.source, home.generate = source, generate
    home.submit_message(
        {
            "request_id": "build-youtube",
            "body": "Develop the YouTube tool we talked about earlier based on the report. "
            "This shouldn't be part of Raticode. Make it a daily report for my Second Brain.",
        }
    )
    home._reply()
    task = next(t for t in home.store.tasks() if t["title"] == "Daily YouTube script")
    assert task["project_dir"] is None and task["schedule"]["kind"] == "once"
    assert len(calls) == 2
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    for options in observed:
        assert options["working_dir"] == home.data_dir / "rem-work" / task["id"]
        assert "captions-service-v2" in options["agent_instructions"]
        assert "youtube-goal" in options["agent_instructions"]
        assert "Second Brain" in options["agent_instructions"]
    assert home.store.thread(run["thread_id"])["messages"][0]["body"] == goal
    assert home.store.thread(run["thread_id"])["metadata"]["archived"] is False
    later = home.edit_task(task["id"], {"request_id": "run-again"}, "run")["run"]
    assert home._work_root(later) == home._work_root(run)
    assert home._delegation_context(later, {})


def test_worker_background_stays_fixed_for_a_run_attempt(home: RemHome, monkeypatch):
    # Recall reads live chat. Changing text would resend all instructions on resume.
    recalled = iter([{"recalled_context": [n]} for n in range(3)])
    monkeypatch.setattr(home, "_delegation_context", lambda run, config: next(recalled))
    run = {"id": "run-1", "epoch": 1}

    first = home._worker_background(run, {})
    assert home._worker_background(run, {}) == first == {"recalled_context": [0]}
    assert home._worker_background({**run, "epoch": 2}, {}) == {"recalled_context": [1]}


@pytest.mark.parametrize("mode", ["mixed", "exhausted", "cancelled"])
def test_recall_cannot_execute_actions_or_run_without_a_final_decision(home: RemHome, mode: str):
    calls = []

    def generate(prompt, config, cancel):
        calls.append(prompt)
        response = {"recall": {"queries": ["Earlier"]}}
        if mode == "mixed":
            response.update(reply="Done", tasks=[recipe()])
        if mode == "cancelled":
            cancel.set()
        return response

    home.generate = generate
    home.submit_message({"request_id": "recall", "body": "Build the thing from earlier"})
    home._reply()
    assert not home.store.tasks()
    assert home.store.conversation()["conversation"]["turn_state"] == "idle"
    # A mixed reply is treated as the read it asked for, so it uses the reads left.
    assert len(calls) == (1 if mode == "cancelled" else 3)


def test_rem_repairs_invalid_internal_task_fields_without_reasking_user(home: RemHome):
    calls = []

    def generate(prompt, config, cancel):
        calls.append(prompt)
        if len(calls) == 1:
            return {"reply": "I'll build it.", "tasks": [recipe(title="")]}
        assert "Repair the decision" in prompt
        return {"reply": "I'll build it.", "tasks": [recipe(title="Standalone script")]}

    home.generate = generate
    home.submit_message({"request_id": "repair", "body": "Build a standalone script now"})
    home._reply()
    assert len(calls) == 2
    assert home.store.tasks()[0]["project_dir"] is None
    assert len(home.store.conversation()["messages"]) == 2


def test_chat_task_updates_refresh_original_request_and_retrieval_references(home: RemHome):
    task = home.create_task({"request_id": "old-task", **recipe(title="Standalone script")})["task"]
    home.generate = lambda p, c, e: {
        "reply": "I'll update it using our earlier discussion.",
        "controls": [
            {
                "task_id": task["id"],
                "action": "update",
                "patch": {"prompt": "Generate daily reports at 6AM"},
            }
        ],
    }
    home.submit_message(
        {"request_id": "update-script", "body": "Use the earlier report and make it run at 6AM"}
    )
    home._reply()
    updated = home.store.tasks()[0]
    assert updated["delegation_context"]["source_message_id"] == "update-script"
    assert (
        updated["active_run"]["pending_recipe"]["delegation_context"]
        == updated["delegation_context"]
    )
    context = home._delegation_context({"recipe": updated}, {})
    assert context["original_request"]["body"] == "Use the earlier report and make it run at 6AM"


@pytest.mark.parametrize("retry", ["chat", "button", "legacy"])
def test_review_launch_failure_resumes_review_without_repeating_development(
    home: RemHome, tmp_path: Path, retry: str
):
    original = home.source
    workers, reviews = [], []

    async def source(**kwargs):
        if "independent read-only reviewer" in kwargs["agent_instructions"]:
            reviews.append(kwargs)
            if len(reviews) == 1:
                raise OSError(7, "Argument list too long", "/fake/codex")
        else:
            workers.append(kwargs)
        async for event in original(**kwargs):
            yield event

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    blocked = wait_run(home, task["id"], {"blocked"})
    assert blocked["worker_result"] == "Implemented and tested."
    assert blocked["blocked_phase"] == "verifying"
    assert "implementation finished" in home.store.conversation()["messages"][-1]["body"]
    assert "Argument list too long" in blocked["error_detail"]
    if retry == "chat":

        def generate(prompt, config, cancel):
            assert "worker_result" in prompt and "Implemented and tested." in prompt
            return {
                "reply": "I'll retry its review.",
                "controls": [{"task_id": task["id"], "action": "retry_review"}],
            }

        home.generate = generate
        home.submit_message({"request_id": "retry-review", "body": "Retry the review"})
        home._reply()
        assert (
            home.store.conversation()["messages"][-1]["work"]["thread_id"] == blocked["thread_id"]
        )
    else:
        if retry == "legacy":
            with home.store.connect() as db:
                blocked.pop("blocked_phase")
                home.store.put(db, "runs", blocked)
        request = {"request_id": "retry-button"}
        resumed = home.edit_task(task["id"], request, "run")
        assert home.edit_task(task["id"], request, "run") == resumed
        assert resumed["run"]["id"] == blocked["id"]
    finished = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert finished["state"] == "succeeded", finished
    assert finished["thread_id"] == blocked["thread_id"]
    assert finished["verification_thread_id"] == blocked["verification_thread_id"]
    assert len(workers) == 1 and len(reviews) == 2
    assert home.store.thread(blocked["thread_id"])["metadata"]["archived"] is False


def test_main_chat_answer_resumes_blocked_worker_and_rechecks_work(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    home.store.append_thread(
        run["thread_id"],
        {
            "id": "question",
            "role": "assistant",
            "kind": "final",
            "body": "Which account should I use?",
        },
    )
    home._finish(run["id"], "blocked", "Which account should I use?", 0)

    def generate(prompt, config, cancel):
        assert "follow_up" in prompt and "Which account should I use?" in prompt
        return {
            "reply": "I'll pass that answer along.",
            "controls": [
                {
                    "task_id": task["id"],
                    "action": "follow_up",
                    "message": "Use the already connected account.",
                }
            ],
        }

    home.generate = generate
    home.submit_message({"request_id": "answer", "body": "Use the already connected account"})
    home._reply()
    updated = home.store.tasks()[0]
    assert updated["active_run"]["id"] == run["id"]
    assert home.store.conversation()["messages"][-1]["work"]["thread_id"] == run["thread_id"]
    assert len(home.store.tasks()) == 1
    finished = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert finished["state"] == "succeeded", finished
    assert finished["epoch"] == 1
    assert any(
        "already connected account" in m["body"]
        for m in home.store.thread(run["thread_id"])["messages"]
    )
    replies = [
        m for m in home.store.thread(run["thread_id"])["messages"] if m.get("sender") == "rem"
    ]
    assert replies[0]["body"] == "Use the already connected account."


def test_followup_controls_need_an_actual_reply_to_worker():
    from gofer.ui.rem_home_models import TaskControl

    with pytest.raises(ValueError):
        TaskControl(task_id="task", action="follow_up")
    with pytest.raises(ValueError):
        TaskControl(task_id="task", action="run", message="Not a follow-up")


@pytest.mark.parametrize("action", ["follow_up", "retry_review"])
def test_blocked_run_cannot_resume_over_another_active_run(
    home: RemHome, tmp_path: Path, action: str
):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    home._finish(run["id"], "blocked", "Needs an answer", 0)
    with home.store.connect() as db:
        blocked = home.store.get(db, "runs", run["id"])
        blocked.update(
            worker_result="Implemented and tested.",
            verification_thread_id="review",
            blocked_phase="verifying",
        )
        home.store.put(db, "runs", blocked)
        home.store.add_run(db, task, "manual:newer", home.store.setting(db, "config"))
    if action == "follow_up":
        result = home.instruct(run["thread_id"], {"request_id": "resume", "body": "Continue"})
        assert result["run"]["id"] != run["id"]
        assert result["run"]["thread_id"] == run["thread_id"]
    else:
        with pytest.raises(RemConflict, match="already has an active"):
            home.retry_review(run["id"], {"request_id": "resume"})
    with home.store.connect() as db:
        assert home.store.get(db, "runs", run["id"])["state"] == "blocked"


@pytest.mark.parametrize("proposal", ["cancel", "duplicate"])
def test_continue_stopped_task_reuses_threads_instead_of_following_stale_commands(
    home: RemHome, tmp_path: Path, proposal: str
):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    home._finish(run["id"], "cancelled", "Stopped the current run.", 0)

    def generate(prompt, config, cancel):
        assert '"current_request": {"body": "continue working that."' in prompt
        return (
            {"reply": "Stopped.", "controls": [{"task_id": task["id"], "action": "cancel"}]}
            if proposal == "cancel"
            else {"reply": "New task", "tasks": [recipe(tmp_path)]}
        )

    home.generate = generate
    home.submit_message({"request_id": "continue", "body": "continue working that."})
    home._reply()
    home._reply()  # Reconciliation must not replay an already processed request.
    current = home.store.tasks()
    assert len(current) == 1
    assert current[0]["active_run"]["id"] == run["id"]
    finished = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert finished["state"] == "succeeded"
    assert finished["thread_id"] == run["thread_id"]
    with home.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 1


def test_clear_history_erases_context_and_journals_but_keeps_user_files(
    home: RemHome, tmp_path: Path
):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    finished = wait_run(home, task["id"], {"succeeded"})
    old_conversation = home.store.conversation()["conversation"]["id"]
    with home.store.connect() as db:
        home.store.set_setting(db, "memories", {"old": {"text": "corrupted old context"}})
        config = home.store.setting(db, "config")
        config["workspace_snapshot"] = {"threads": [{"id": finished["thread_id"]}]}
        home.store.set_setting(db, "config", config)
        ids = [r[0] for r in db.execute("SELECT id FROM threads")]
    from gofer.ui.chat_sessions import _digest

    for identity in ids:
        private = home.data_dir / "chat-provider-sessions" / _digest(identity)
        private.mkdir(parents=True)
        (private / "session.json").write_text("private context")
    manual = home.jobs.directory / "manual-thread"
    manual.mkdir(parents=True)
    (manual / "journal.jsonl").write_text("User's own thread")
    (tmp_path / "implementation.py").write_text("completed implementation")
    request = {"request_id": "clear", "conversation_id": old_conversation, "confirm": True}
    cleared = home.clear_history(request)
    assert set(cleared["removed_thread_ids"]) == set(ids)
    assert cleared["conversation"]["id"] != old_conversation
    assert home.clear_history(request)["conversation"] == cleared["conversation"]
    assert home.store.conversation()["messages"] == []
    assert home.store.tasks() == []
    with home.store.connect() as db:
        for table in ("runs", "revisions", "threads", "thread_messages", "receipts", "events"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert not home.store.setting(db, "memories")
        assert "workspace_snapshot" not in home.store.setting(db, "config")
    for identity in ids:
        assert not (home.jobs.directory / identity).exists()
        assert not (home.steering.directory / f"{identity}.json").exists()
        assert not (home.data_dir / "chat-provider-sessions" / _digest(identity)).exists()
    assert (manual / "journal.jsonl").exists()
    assert (tmp_path / "implementation.py").exists()
    home.tick()
    assert RemHomeStore(home.data_dir).tasks() == []
    assert not home.store.conversation()["messages"]
    with pytest.raises(RemConflict):
        home.clear_history({**request, "request_id": "stale-window"})


def test_clear_history_drains_worker_and_conversation_before_erasing(home: RemHome, tmp_path: Path):
    entered = threading.Event()
    thinking = threading.Event()

    async def source(**kwargs):
        entered.set()
        while not kwargs["cancel_event"].is_set():
            await asyncio.sleep(0.01)
        yield {"type": "stopped"}

    def generate(prompt, config, cancel):
        thinking.set()
        cancel.wait(5)
        return {"reply": "stale result", "tasks": [recipe(tmp_path)]}

    home.source = source
    home.generate = generate
    home.create_task({"request_id": "create", **recipe(tmp_path)})
    home.tick()  # Acquire the lease first so startup recovery doesn't claim the message.
    home.submit_message({"request_id": "message", "body": "Start another task"})
    home.tick()
    assert entered.wait(3) and thinking.wait(3)
    identity = home.store.conversation()["conversation"]["id"]
    home.clear_history({"request_id": "clear", "conversation_id": identity, "confirm": True})
    home.tick()
    assert home.jobs.active_snapshot() == []
    assert home.store.tasks() == []
    assert home.store.conversation()["messages"] == []


def test_a_clear_that_gives_up_answers_the_message_it_interrupted(
    home: RemHome, monkeypatch: pytest.MonkeyPatch
):
    import gofer.ui.rem_home as rem_home_module

    class Stuck:
        def is_alive(self) -> bool:
            return True

    class Clock:
        now = 0.0

        def monotonic(self) -> float:
            self.now += 100
            return self.now

        def sleep(self, _seconds: float) -> None:
            pass

        def time(self) -> float:
            return time.time()

    home.tick()
    home.submit_message({"request_id": "asked", "body": "What is running?"})
    home.workers["stuck"] = (Stuck(), threading.Event())  # type: ignore[assignment]
    monkeypatch.setattr(rem_home_module, "time", Clock())
    identity = home.store.conversation()["conversation"]["id"]
    with pytest.raises(RemConflict, match="has not stopped yet"):
        home.clear_history({"request_id": "clear", "conversation_id": identity, "confirm": True})
    monkeypatch.setattr(rem_home_module, "time", time)
    home.workers.pop("stuck")
    state = home.store.conversation()["conversation"]
    # The message wasn't answered, so it isn't marked handled.
    assert state["turn_state"] == "replying" and state.get("processed") != "asked"
    home.tick()
    home.conversation_worker.join(timeout=5)
    replies = [m["body"] for m in home.store.conversation()["messages"] if m["id"] == "reply-asked"]
    assert replies == ["Hello."]


def test_startup_does_not_discard_a_message_submitted_before_first_tick(home: RemHome):
    home.submit_message({"request_id": "fresh", "body": "Hello"})
    home.tick()
    assert home.conversation_worker is not None
    home.conversation_worker.join(3)
    snapshot = home.store.conversation()
    assert [m["body"] for m in snapshot["messages"]] == ["Hello", "Hello."]
    assert snapshot["conversation"]["processed"] == "fresh"


def test_restart_replays_saved_conversation_without_resending_or_duplicate_tasks(
    home: RemHome, tmp_path: Path
):
    sent = {"request_id": "saved", "body": "Research the existing tool"}
    home.submit_message(sent)
    with home.store.connect() as db:
        conversation = home.store.setting(db, "conversation")
        conversation.pop("reply_owner")  # A previous backend/version owned this decision.
        home.store.set_setting(db, "conversation", conversation)
    home.generate = lambda *args: {
        "reply": "I'll check the existing work.",
        "tasks": [recipe(tmp_path)],
    }
    home._recover()
    home._reply()
    home._recover()
    home._reply()
    assert len(home.store.tasks()) == 1
    assert home.store.conversation()["conversation"]["processed"] == "saved"
    assert len([m for m in home.store.conversation()["messages"] if m["role"] == "user"]) == 1
    assert not any("resend" in m["body"] for m in home.store.conversation()["messages"])


def test_closing_mid_reply_leaves_the_request_for_the_next_backend(home: RemHome):
    def closing(prompt, config, cancel):
        home.close()  # Raticode quits while the chat model is still writing.
        raise InterruptedError("Rem stopped")

    home.generate = closing
    home.submit_message({"request_id": "late", "body": "What's on my list?"})
    home._reply()
    state = home.store.conversation()
    assert state["conversation"]["turn_state"] == "replying"
    assert not any(m["id"] == "reply-late" for m in state["messages"])

    restarted = RemHome(
        home.data_dir,
        source=home.source,
        jobs=home.jobs,
        steering=home.steering,
        authorize=lambda c, r: None,
        generate=lambda *args: {"reply": "Two tasks are running."},
        start=False,
    )
    try:
        restarted._recover()
        restarted._reply()
        state = restarted.store.conversation()
        replies = [m["body"] for m in state["messages"] if m["id"] == "reply-late"]
        assert replies == ["Two tasks are running."]
        assert state["conversation"]["processed"] == "late"
    finally:
        restarted.close()


def test_a_phone_message_sent_while_rem_replies_waits_and_is_answered_next(home: RemHome):
    # The desktop window waits and resends by itself; a phone, an alert reply or voice
    # mode used to get "Rem is still replying; try again shortly" instead.
    prompts: list[str] = []

    def generate(prompt, config, cancel):
        prompts.append(prompt)
        return {"reply": f"Answer {len(prompts)}."}

    home.generate = generate
    home.submit_message({"request_id": "first", "body": "What is running?"})
    queued = home.submit_message(
        {"request_id": "second", "body": "Also, what finished overnight?"}, source="phone"
    )
    assert queued == {"accepted": True, "request_id": "second", "waiting": True}
    # Resending the same request, as a phone does after a dropped link, keeps one copy.
    again = home.submit_message(
        {"request_id": "second", "body": "Also, what finished overnight?"}, source="phone"
    )
    assert again == queued
    # A message from the desktop window waits too, so a reloaded window can't lose it.
    assert home.submit_message({"request_id": "desk", "body": "From the desktop"})["waiting"]
    state = home.store.conversation()
    assert [m["id"] for m in state["waiting"]] == ["second", "desk"]
    assert "waiting" not in state["conversation"]
    assert state["waiting"][0]["waiting"] and state["waiting"][0]["role"] == "user"
    assert "second" not in {m["id"] for m in state["messages"]}

    home._reply()
    # Rem answered the first request without seeing one it hadn't reached yet.
    assert "what finished overnight" not in prompts[0]
    for _ in range(2):
        home.tick()
        if home.conversation_worker:
            home.conversation_worker.join(timeout=5)
    state = home.store.conversation()
    assert not state["waiting"]
    assert [(m["id"], m["body"]) for m in state["messages"]] == [
        ("first", "What is running?"),
        ("reply-first", "Answer 1."),
        ("second", "Also, what finished overnight?"),
        ("reply-second", "Answer 2."),
        ("desk", "From the desktop"),
        ("reply-desk", "Answer 3."),
    ]
    assert state["messages"][2]["source"] == "phone"
    assert state["conversation"]["processed"] == "desk"


def test_a_phone_can_leave_only_a_few_messages_waiting(home: RemHome):
    import gofer.ui.rem_home as rem_home_module

    home.submit_message({"request_id": "busy", "body": "Start"})
    for index in range(rem_home_module.MAX_WAITING_MESSAGES):
        home.submit_message({"request_id": f"w{index}", "body": f"Next {index}"}, source="phone")
    with pytest.raises(RemConflict, match="messages waiting"):
        home.submit_message({"request_id": "extra", "body": "One more"}, source="phone")


def test_each_model_call_in_a_reply_starts_its_shown_draft_over(home: RemHome):
    seen: list[tuple[Any, Any]] = []

    def generate(prompt, config, cancel):
        conversation = home.store.conversation()["conversation"]
        seen.append((conversation.get("draft_attempt"), conversation.get("draft_reply")))
        home._draft_reply("I’ll have a worker look into the earlier tool and report back.")
        if len(seen) == 1:
            return {"reply": "Starting it.", "tasks": [recipe(title="")]}
        return {"reply": "Starting it.", "tasks": [recipe(title="Check the tool")]}

    home.generate = generate
    home.submit_message({"request_id": "draft", "body": "Check the tool"})
    home._reply()
    # The repair call began with no text shown and a new attempt number.
    assert seen == [(1, None), (2, None)]


def test_a_failed_reply_keeps_the_decision_that_failed_for_tracing(home: RemHome):
    bad = {
        "reply": "I’ll update it.",
        "controls": [{"task_id": "made-up", "action": "follow_up", "message": "Go"}],
    }
    home.generate = lambda *args: bad
    home.submit_message({"request_id": "trace", "body": "Update the task"})
    home._reply()
    shown = next(m for m in home.store.conversation()["messages"] if m["id"] == "reply-trace")
    assert shown["body"].startswith("I couldn’t finish that request")
    # The apps never receive it; it stays in Rem's database for tracing.
    assert "diagnostic" not in shown
    with home.store.read() as db:
        row = db.execute("SELECT body FROM messages WHERE id='reply-trace'").fetchone()
    reply = json.loads(row[0])
    assert reply["diagnostic"]["error"].startswith("UnusableControl: ")
    assert json.loads(reply["diagnostic"]["decision"]) == bad


def test_a_conversation_repair_note_applies_to_one_answer_only(home: RemHome):
    async def source(**kwargs):
        yield {"type": "final", "message": {"body": "Doing well, and you?"}}

    checks: list[str] = []

    def generate(prompt, config, cancel):
        checks.append(prompt)
        good = {
            "criteria": [{"id": "check", "result": "pass", "evidence": "Friendly chat"}],
            "summary": "We had a brief chat.",
        }
        if len(checks) == 1:
            return {"criteria": [], "next_message": "", "summary": "Chatted."}
        return {**good, "next_message": "Busy, thanks." if len(checks) == 3 else ""}

    home.source, home.generate = source, generate
    task = home.create_task(
        {
            "request_id": "chat",
            **recipe(type="errand", execution_mode="conversation", conversation_turns=2),
        }
    )["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert "couldn't be used" in checks[1]
    # Asking for the next exchange no longer repeats the note about the fixed answer.
    assert "Supply your actual reply" in checks[2] and "couldn't be used" not in checks[2]


def test_a_malformed_conversation_verdict_gets_one_more_try(home: RemHome):
    async def source(**kwargs):
        yield {"type": "final", "message": {"body": "All good here, thanks for asking."}}

    checks: list[str] = []

    def generate(prompt, config, cancel):
        checks.append(prompt)
        if len(checks) == 1:
            return {"criteria": [], "next_message": "", "summary": "Chatted."}
        return {
            "criteria": [{"id": "check", "result": "pass", "evidence": "Friendly chat"}],
            "next_message": "",
            "summary": "We had a brief chat.",
        }

    home.source, home.generate = source, generate
    task = home.create_task(
        {"request_id": "chat", **recipe(type="errand", execution_mode="conversation")}
    )["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert len(checks) == 2 and "couldn't be used" in checks[1]


def test_an_empty_discussion_reply_is_retried(home: RemHome, instant_retries: None):
    turns = 0

    async def source(**kwargs):
        nonlocal turns
        turns += 1
        yield {"type": "final", "message": {"body": "" if turns == 1 else "Doing well."}}

    home.source = source
    home.generate = lambda *args: {
        "criteria": [{"id": "check", "result": "pass", "evidence": "Friendly chat"}],
        "next_message": "",
        "summary": "We had a brief chat.",
    }
    task = home.create_task(
        {"request_id": "chat", **recipe(type="errand", execution_mode="conversation")}
    )["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert (turns, run["transient_retries"]) == (2, 1)


def test_research_review_ignores_bookkeeping_and_reports_next_steps_in_chat(
    home: RemHome, tmp_path: Path
):
    from gofer.ui.rem_home_artifacts import artifact_tools

    archive = tmp_path / "Rem Conversations"
    archive.mkdir()
    bookkeeping = tmp_path / ".raticode"
    bookkeeping.mkdir()
    home.configure(
        {
            "provider": "codex",
            "brain": {"enabled": True, "root": str(tmp_path)},
            "conversation_archive_root": str(archive),
        }
    )
    original = home.source
    reviews = []
    steps = (
        "The digest and workflow exist. Next, connect your YouTube account, "
        "install the transcript adapter, and enable the 6 AM schedule."
    )

    async def source(**kwargs):
        if "independent read-only reviewer" in kwargs["agent_instructions"]:
            reviews.append(kwargs)
            (bookkeeping / "second-brain.sqlite3").write_bytes(b"index refresh")
            (bookkeeping / "brain.json").write_text('{"generation":2}')
            (archive / "index.json").write_text("archived review thought")
        else:
            run = home.store.tasks()[0]["active_run"]
            with artifact_tools(home, run, "execute"):
                # A saved artifact receipt, independent of the summary provider.
                report = tmp_path / "reports" / "rem" / "audit.md"
                report.write_text("# Verified audit\nDigest exists; OAuth and timer need setup.")
                import hashlib

                home._update(
                    run["id"],
                    artifacts=[
                        {
                            "path": str(report),
                            "sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
                            "epoch": run["epoch"],
                            "link": f"[Saved audit](<{report}>)",
                        }
                    ],
                )
        async for event in original(**kwargs):
            yield event

    home.source = source
    home.generate = lambda *args: {"summary": steps, "needs_user": False}
    task = home.create_task({"request_id": "research", **recipe(tmp_path, type="research")})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert len(reviews) == 1
    update = home.store.conversation()["messages"][-1]
    assert update["concise"]
    assert steps in update["body"]
    assert "[Saved audit]" in update["body"]
    assert not run["needs_user"]


def test_changing_source_refreshes_only_review_and_reuses_threads(home: RemHome, tmp_path: Path):
    source_file = tmp_path / "source.py"
    source_file.write_text("value = 1")
    original = home.source
    workers, reviews = [], []

    async def source(**kwargs):
        if "independent read-only reviewer" in kwargs["agent_instructions"]:
            reviews.append(kwargs)
            if len(reviews) == 1:
                source_file.write_text("value = 2")
        else:
            workers.append(kwargs)
        async for event in original(**kwargs):
            yield event

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert len(workers) == 1 and len(reviews) == 2
    assert reviews[0]["conversation_id"] == reviews[1]["conversation_id"]


def test_human_question_is_answered_from_memories_before_interrupting_user(
    home: RemHome, tmp_path: Path
):
    with home.store.connect() as db:
        home.store.set_setting(
            db, "memories", {"schedule": {"text": "User chose 6AM America/New_York"}}
        )
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    attempts = []

    def generate(prompt, config, cancel):
        attempts.append(prompt)
        if "Before asking the human" in prompt:
            assert "6AM America/New_York" in prompt
            return {
                "needs_user": False,
                "next_message": "Use the previously chosen 6AM America/New_York schedule.",
            }
        return {"needs_user": True, "user_question": "What time should this run?"}

    home.generate = generate
    reply = home._answer_worker(
        task["active_run"],
        "What time should this run?",
        "Waiting for the schedule",
        threading.Event(),
    )
    assert not reply.needs_user
    assert "6AM" in reply.next_message
    assert len(attempts) == 2


def test_restart_checks_uncertain_progress_read_only_before_repeating_actions(
    home: RemHome, tmp_path: Path
):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    home._update(task["active_run"]["id"], state="executing")
    home.store.append_thread(
        task["active_run"]["thread_id"],
        {"id": "saved-reply", "role": "assistant", "body": "The installed feature passes tests."},
    )
    original = home.source
    turns = []

    async def source(**kwargs):
        turns.append(kwargs)
        assert "restart recovery" in kwargs["agent_instructions"]
        assert "The installed feature passes tests." in kwargs["agent_instructions"]
        assert kwargs["permission_mode"] == "read-only"
        async for event in original(**kwargs):
            yield event

    home.source = source
    home._recover()
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert len(turns) == 1
    assert run["thread_id"] == task["active_run"]["thread_id"]


def test_obsolete_project_change_block_recovers_review_once_without_repeating_work(
    home: RemHome, tmp_path: Path
):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    saved = task["active_run"]
    home._update(
        saved["id"],
        state="verifying",
        worker_result="Implemented and tested.",
        worker_traces=[
            {"category": "shell", "phase": "result", "status": "completed", "exitCode": 0}
        ],
        verification_thread_id="saved-review",
    )
    home._finish(
        saved["id"],
        "blocked",
        "The project changed during review. Run it again for fresh checks.",
        0,
    )
    original = home.source
    turns = []

    async def source(**kwargs):
        turns.append(kwargs)
        assert "independent read-only reviewer" in kwargs["agent_instructions"]
        async for event in original(**kwargs):
            yield event

    home.source = source
    home._recover()
    recovered = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert recovered["state"] == "succeeded", recovered
    assert recovered["thread_id"] == saved["thread_id"]
    assert turns[0]["conversation_id"] == saved["thread_id"] + "-review"
    home._recover()
    assert len(turns) == 1
    assert home.store.conversation()["messages"][-1]["body"] == "Implemented and tested."


def test_stale_outcome_delivered_late_does_not_follow_the_newer_one(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    home._update(run["id"], state="verifying", worker_result="Implemented and tested.")
    home._finish(run["id"], "blocked", "The project changed during review.", 0)
    # Recovery resumes the review under a new epoch.
    force_run(home, run["id"], state="verifying", epoch=1)
    home._finish(run["id"], "succeeded", "Implemented and tested.", 1)
    stale, newer = home.store.outbox()
    assert (stale["state"], newer["state"]) == ("blocked", "succeeded")
    # Concurrent delivery threads can finish in either order.
    home._deliver(newer)
    home._deliver(stale)
    assert home.store.outbox() == []
    bodies = [m["body"] for m in home.store.conversation()["messages"]]
    assert bodies[-1] == "Implemented and tested."
    assert not any("The project changed during review." in body for body in bodies)


def test_essential_user_question_includes_saved_result_in_main_chat(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    home._update(run["id"], worker_result="The feed works with public videos.")
    home._finish(
        run["id"],
        "blocked",
        "Do you authorize paying $20 for the subscription?",
        0,
        needs_user=True,
    )
    home.deliver_outcomes()
    saved = home.store.tasks()[0]["last_run"]
    update = home.store.conversation()["messages"][-1]
    assert saved["needs_user"]
    assert saved["user_question"] in update["body"]
    assert "The feed works with public videos." in update["body"]
    assert update["work"]["needs_user"]


def test_repeated_unfinished_task_proposal_reuses_existing_thread(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    home._finish(run["id"], "blocked", "Needs a token.", 0)

    def generate(prompt, config, cancel):
        return {"reply": "Created it.", "tasks": [recipe(tmp_path, prompt="Use the new token")]}

    home.generate = generate
    home.submit_message({"request_id": "again", "body": "I added the token, build the feature"})
    home._reply()
    current = home.store.tasks()
    assert len(current) == 1
    assert current[0]["active_run"]["id"] == run["id"]
    assert "Use the new token" in current[0]["active_run"]["recipe"]["prompt"]
    reply = home.store.conversation()["messages"][-1]
    assert reply["body"] == "I’ll continue in the existing thread."
    assert reply["work"]["thread_id"] == run["thread_id"]
    finished = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert finished["thread_id"] == run["thread_id"]
    with home.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 1


def test_workflow_follow_up_upgrades_research_scope_and_reuses_task_thread(
    home: RemHome, tmp_path: Path
):
    task = home.create_task({"request_id": "audit", **recipe(tmp_path, type="research")})["task"]
    first = task["active_run"]
    home._finish(first["id"], "cancelled", "Saved the audit.", 0)
    original = home.source
    turns = []

    async def source(**kwargs):
        turns.append(kwargs)
        async for event in original(**kwargs):
            yield event

    home.source = source
    home.generate = lambda *args: {
        "reply": "I'll run the workflow and report what happens.",
        "controls": [
            {
                "task_id": task["id"],
                "action": "follow_up",
                "message": "Run the registered workflow once and report its output or failure.",
                "patch": {
                    "type": "errand",
                    "acceptance_criteria": [
                        {"id": "check", "description": "Report the actual execution result."}
                    ],
                },
            }
        ],
    }
    home.submit_message(
        {
            "request_id": "run",
            "body": "Run the workflow and link its report or tell me what went wrong.",
        }
    )
    home._reply()
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert run["thread_id"] == first["thread_id"]
    assert run["recipe"]["type"] == home.store.tasks()[0]["type"] == "errand"
    assert turns[0]["permission_mode"] == "workspace-write"
    assert "Run the registered workflow once" in turns[0]["messages"][-1]["body"]
    assert turns[0]["conversation_id"] != turns[1]["conversation_id"]
    assert all(m.get("worker_role") == "verify" for m in turns[1]["messages"])
    with home.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 1


def test_follow_up_on_a_repeating_task_changes_only_that_run(home: RemHome, tmp_path: Path):
    # 2026-11-01: a one-off question asked in the nightly audit's thread became a
    # follow_up with patch {type: errand, ...}. The patch was saved to the task,
    # so every later 4 AM run asked the question again instead of auditing.
    task = home.create_task(
        {
            "request_id": "nightly",
            **recipe(
                tmp_path,
                type="maintain",
                prompt="Audit both apps every night",
                schedule={"kind": "daily", "local_time": "04:00", "timezone": "UTC"},
            ),
        }
    )["task"]
    home.store.due(datetime.now(UTC) + timedelta(days=2))
    nightly = wait_run(home, task["id"], {"succeeded"})
    decisions = [
        {
            "reply": "I'll ask the audit worker.",
            "controls": [
                {
                    "task_id": task["id"],
                    "action": "follow_up",
                    "message": "Who set the 400 kB budget?",
                    "patch": {
                        "type": "errand",
                        "prompt": "Who set the 400 kB budget?",
                        "acceptance_criteria": [{"id": "check", "description": "Name who set it."}],
                    },
                }
            ],
        }
    ]
    summarize = home.generate
    # The decision answers the chat message; later calls write task summaries.
    home.generate = lambda *args: decisions.pop() if decisions else summarize(*args)
    home.submit_message({"request_id": "ask", "body": "Who set the budget at 400kb?"})
    home._reply()
    asked = wait_run(home, task["id"], {"succeeded"})
    assert asked["id"] != nightly["id"]
    assert asked["thread_id"] == nightly["thread_id"]
    assert asked["recipe"]["type"] == "errand"
    assert asked["recipe"]["acceptance_criteria"][0]["description"] == "Name who set it."

    saved = home.store.tasks()[0]
    assert saved["revision"] == 1
    assert (saved["type"], saved["prompt"]) == ("maintain", "Audit both apps every night")
    assert saved["acceptance_criteria"][0]["description"] == "It works"
    with home.store.read() as db:
        assert db.execute("SELECT count(*) FROM revisions").fetchone()[0] == 1

    home.store.due(datetime.fromisoformat(saved["next_occurrence_at"]) + timedelta(minutes=1))
    next_night = wait_run(home, task["id"], {"succeeded"})
    assert next_night["id"] not in {nightly["id"], asked["id"]}
    assert next_night["recipe"]["type"] == "maintain"
    assert next_night["recipe"]["prompt"] == "Audit both apps every night"
    assert next_night["thread_id"] == nightly["thread_id"]


def test_follow_up_during_a_repeating_run_leaves_the_task_alone(home: RemHome, tmp_path: Path):
    task = home.create_task(
        {
            "request_id": "nightly",
            **recipe(
                tmp_path,
                type="maintain",
                prompt="Audit both apps every night",
                schedule={"kind": "daily", "local_time": "04:00", "timezone": "UTC"},
            ),
        }
    )["task"]
    with home.store.connect() as db:
        run = home.store.add_run(db, home.store.get(db, "tasks", task["id"]), "occ", {})
    result = home.instruct(
        run["thread_id"],
        {"request_id": "ask", "body": "Answer one question instead."},
        recipe_patch={"type": "errand", "prompt": "Answer one question instead."},
    )
    assert result["run"]["pending_recipe"]["type"] == "errand"
    assert result["run"]["pending_recipe"]["revision"] == 1
    saved = home.store.tasks()[0]
    assert (saved["revision"], saved["type"]) == (1, "maintain")
    assert saved["prompt"] == "Audit both apps every night"


def cards(home: RemHome, run_id: str) -> list[dict[str, Any]]:
    """Conversation messages that show a task card for one run."""
    return [
        m
        for m in home.store.conversation()["messages"]
        if (m.get("work") or {}).get("run_id") == run_id
    ]


def test_chat_follow_up_after_a_finished_run_shows_the_task_once(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    first = wait_run(home, task["id"], {"succeeded"})
    home.generate = lambda *args: {
        "reply": "I’ll have the worker build a debug APK.",
        "controls": [{"task_id": task["id"], "action": "follow_up", "message": "Build an APK"}],
    }
    home.submit_message({"request_id": "apk", "body": "Generate a new APK too."})
    home._reply()
    follow_up = home.store.tasks()[0]["active_run"]
    assert follow_up["id"] != first["id"]
    shown = cards(home, follow_up["id"])
    assert [(m["id"], m["body"]) for m in shown] == [
        ("reply-apk", "I’ll have the worker build a debug APK.")
    ]
    # The first run keeps its own card and updates; they're a different run.
    assert any(m["id"] == f"queued-{first['id']}" for m in cards(home, first["id"]))


def test_thread_follow_up_after_a_finished_run_shows_the_task_once(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"succeeded"})
    follow_up = home.instruct(run["thread_id"], {"request_id": "again", "body": "Add a case"})
    assert [m["body"] for m in cards(home, follow_up["run"]["id"])] == [
        "I’ll follow up and check the result."
    ]


def test_one_reply_following_up_two_finished_tasks_keeps_one_card_for_each(
    home: RemHome, tmp_path: Path
):
    first = home.create_task({"request_id": "a", **recipe(tmp_path, title="Task A")})["task"]
    wait_run(home, first["id"], {"succeeded"})
    second = home.create_task({"request_id": "b", **recipe(tmp_path, title="Task B")})["task"]
    wait_run(home, second["id"], {"succeeded"})
    home.generate = lambda *args: {
        "reply": "I’ll follow up on both.",
        "controls": [
            {"task_id": first["id"], "action": "follow_up", "message": "Add a case"},
            {"task_id": second["id"], "action": "follow_up", "message": "Add a case"},
        ],
    }
    home.submit_message({"request_id": "both", "body": "Add a case to both."})
    home._reply()
    runs = {t["title"]: t["active_run"] for t in home.store.tasks()}
    assert [m["body"] for m in cards(home, runs["Task A"]["id"])] == [
        "I’ll follow up and check the result."
    ]
    assert [m["body"] for m in cards(home, runs["Task B"]["id"])] == ["I’ll follow up on both."]


def test_follow_up_beside_a_new_task_keeps_one_card_for_each(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    wait_run(home, task["id"], {"succeeded"})
    home.generate = lambda *args: {
        "reply": "I’ll follow up and start the docs task.",
        "controls": [{"task_id": task["id"], "action": "follow_up", "message": "Add a case"}],
        "tasks": [recipe(tmp_path, title="Write the docs", prompt="Write the docs")],
    }
    home.submit_message({"request_id": "both", "body": "Add a case and write the docs."})
    home._reply()
    runs = {t["title"]: t["active_run"] for t in home.store.tasks()}
    assert len(cards(home, runs["Check the feature"]["id"])) == 1
    assert [m["body"] for m in cards(home, runs["Write the docs"]["id"])] == [
        "I’ll follow up and start the docs task."
    ]


def test_legacy_started_notices_beside_a_reply_are_hidden(tmp_path: Path):
    store = RemHomeStore(tmp_path)

    def say(identity: str, body: str, run: str, title: str, at: str, **extra: Any) -> None:
        work = {"task_id": "t", "run_id": run, "thread_id": "th", "title": title}
        with store.connect() as db:
            store.message(db, body, identity=identity, work=work, created_at=at, **extra)

    # The old follow-up path wrote both of these in one transaction.
    say("queued-r", "Started Align UI.", "r", "Align UI", "2026-11-01T11:50:45.093+00:00")
    say("reply-a", "I’ll build the APK.", "r", "Align UI", "2026-11-01T11:50:45.094+00:00")
    # A scheduled run's notice, its finish and a later reply are distinct updates.
    say("queued-n", "Started Nightly.", "n", "Nightly", "2026-11-01T06:00:00+00:00")
    say("finished-n", "Done.", "n", "Nightly", "2026-11-01T06:00:01+00:00", kind="task_update")
    say("reply-b", "It's still running.", "n", "Nightly", "2026-11-01T06:05:00+00:00")
    # A new task's acknowledgement is Rem's reply itself.
    say("queued-k", "I’ll start the docs.", "k", "Docs", "2026-11-01T07:00:00+00:00")
    say("reply-c", "Also noted.", "k", "Docs", "2026-11-01T07:00:01+00:00")
    shown = [m["id"] for m in store.conversation()["messages"]]
    assert shown == ["reply-a", "queued-n", "finished-n", "reply-b", "queued-k", "reply-c"]
    with store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 7


def test_repeated_occurrences_use_one_thread_per_task(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    first = wait_run(home, task["id"], {"succeeded"})
    with home.store.connect() as db:
        second = home.store.add_run(db, task, "manual:next", home.store.setting(db, "config"))
    assert second["thread_id"] == first["thread_id"]
    with home.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 1


def test_restart_of_shared_thread_review_preserves_worker_result(
    home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    home._update(
        run["id"],
        state="verifying",
        worker_result="The installed tool passes its tests.",
        active_thread_id=run["thread_id"],
        verification_thread_id=run["thread_id"],
        active_turn_id="review-turn",
        active_role="verify",
    )
    monkeypatch.setattr(
        home.jobs,
        "snapshot",
        lambda *args: [{"type": "final", "message": {"body": "Review checks passed."}}],
    )
    home._recover()
    with home.store.connect() as db:
        recovered = home.store.get(db, "runs", run["id"])
    assert recovered["worker_result"] == "The installed tool passes its tests."
    assert recovered["resume_review"]


@pytest.fixture
def instant_retries(monkeypatch: pytest.MonkeyPatch):
    import gofer.ui.rem_home as rem_home_module

    monkeypatch.setattr(rem_home_module, "TRANSIENT_RETRY_SECONDS", (0, 0, 0))
    monkeypatch.setattr(rem_home_module, "GENERATE_RETRY_SECONDS", (0, 0))


def test_transient_provider_failure_retries_the_same_phase(
    home: RemHome, tmp_path: Path, instant_retries: None
):
    original = home.source
    workers: list[dict[str, Any]] = []

    async def source(**kwargs):
        if "independent read-only reviewer" not in kwargs["agent_instructions"]:
            workers.append(kwargs)
            if len(workers) == 1:
                yield {"type": "error", "error": "503 Service Unavailable. Reconnecting 4/5"}
                return
        async for event in original(**kwargs):
            yield event

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"blocked", "succeeded"})
    assert run["state"] == "succeeded", run
    assert run["transient_retries"] == 1
    assert len(workers) == 2
    # The retry starts a fresh provider session that replays the transcript.
    assert workers[1]["reset_session"] is True
    assert "503" in run["error_detail"]


def test_transient_review_failure_retries_review_without_repeating_work(
    home: RemHome, tmp_path: Path, instant_retries: None
):
    original = home.source
    workers, reviews = 0, 0

    async def source(**kwargs):
        nonlocal workers, reviews
        if "independent read-only reviewer" in kwargs["agent_instructions"]:
            reviews += 1
            if reviews == 1:
                raise RuntimeError("Selected model is at capacity. Please try a different model.")
        else:
            workers += 1
        async for event in original(**kwargs):
            yield event

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"blocked", "succeeded"})
    assert run["state"] == "succeeded", run
    assert (workers, reviews) == (1, 2)


def test_persistent_provider_outage_blocks_after_bounded_retries(
    home: RemHome, tmp_path: Path, instant_retries: None
):
    attempts = 0

    async def source(**kwargs):
        nonlocal attempts
        attempts += 1
        yield {"type": "error", "error": "429 Too Many Requests"}

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"blocked", "succeeded"})
    assert run["state"] == "blocked"
    assert attempts == 4
    assert "kept failing after 3 retries" in run["summary_short"]


def test_task_failures_are_not_retried_as_outages(
    home: RemHome, tmp_path: Path, instant_retries: None
):
    attempts = 0

    async def source(**kwargs):
        nonlocal attempts
        attempts += 1
        yield {"type": "error", "error": "Not logged in. Run codex login."}

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"blocked", "succeeded"})
    assert run["state"] == "blocked"
    assert attempts == 1


def test_transient_classification():
    from gofer.ui.rem_home import transient

    assert transient(ValueError("unexpected status 503 Service Unavailable"))
    assert transient(RuntimeError("Anthropic API overloaded"))
    wrapped = ValueError("Rem couldn't prepare its reply")
    wrapped.__cause__ = ConnectionError("Connection reset by peer")
    assert transient(wrapped)
    assert not transient(ValueError("401 Unauthorized"))
    assert not transient(TimeoutError("Worker stop could not be confirmed"))
    assert not transient(ValueError("Reviewer did not return criterion evidence"))


def test_transient_classification_covers_dropped_connections_and_missing_answers():
    from gofer.ui.chat import NO_FINAL_ANSWER
    from gofer.ui.rem_home import transient

    # Claude Code's text once its own retries of a dropped connection run out.
    assert transient(ValueError("API Error: Connection error."))
    assert transient(ValueError("stream error: error sending request for url (https://x)"))
    assert transient(ValueError("getaddrinfo EAI_AGAIN api.anthropic.com"))
    assert transient(ValueError("TypeError: fetch failed"))
    assert transient(ValueError(NO_FINAL_ANSWER))
    assert transient(ValueError("Worker ended without a result"))
    assert not transient(ValueError("Connection settings are invalid"))
    assert not transient(ValueError("Not logged in. Run codex login."))


@pytest.mark.parametrize(
    "first",
    [
        {"type": "error", "error": "API Error: Connection error."},
        {"type": "error", "error": "The provider ended its turn without a final answer."},
        # A turn that finished but whose final reply came back empty.
        {"type": "final", "message": {"body": ""}},
    ],
)
def test_dropped_connections_and_empty_turns_retry_the_worker(
    home: RemHome, tmp_path: Path, instant_retries: None, first: dict[str, Any]
):
    original = home.source
    workers = 0

    async def source(**kwargs):
        nonlocal workers
        if "independent read-only reviewer" not in kwargs["agent_instructions"]:
            workers += 1
            if workers == 1:
                yield first
                return
        async for event in original(**kwargs):
            yield event

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"blocked", "succeeded"})
    assert run["state"] == "succeeded", run
    assert (workers, run["transient_retries"]) == (2, 1)


@pytest.mark.parametrize("ended", ["succeeded", "blocked", "queued"])
def test_follow_up_that_moves_work_into_a_project_continues_in_a_new_thread(
    home: RemHome, tmp_path: Path, ended: str
):
    # 2026-11-03: "implement the voice tidy feature" followed up a research task that
    # had no project. Rem moved the work into the project, and the whole reply failed
    # with "Changing projects requires updating the task first".
    research = home.create_task(
        {"request_id": "research", **recipe(None, type="research", title="Research tidying")}
    )["task"]
    first = research["active_run"]
    if ended != "queued":
        home._finish(first["id"], ended, "Saved the report.", 0)
    home.generate = lambda *args: {
        "reply": "I’ll have a worker implement it in Raticode.",
        "controls": [
            {
                "task_id": research["id"],
                "action": "follow_up",
                "message": "Implement the proposed design.",
                "patch": {
                    "type": "develop",
                    "project_dir": str(tmp_path),
                    "acceptance_criteria": [{"id": "check", "description": "It works"}],
                },
            }
        ],
    }
    home.submit_message({"request_id": "build", "body": "Implement the feature."})
    home._reply()
    reply = next(m for m in home.store.conversation()["messages"] if m["id"] == "reply-build")
    assert reply["body"] == "I’ll have a worker implement it in Raticode."
    run = wait_run(home, research["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert run["thread_id"] != first["thread_id"]
    assert run["recipe"]["project_dir"] == str(tmp_path)
    task = home.store.tasks()[0]
    assert (task["type"], task["project_dir"]) == ("develop", str(tmp_path))
    with home.store.read() as db:
        roots = {
            home.store.get(db, "threads", t)["projectRoot"]
            for t in (first["thread_id"], run["thread_id"])
        }
    assert roots == {"", str(tmp_path)}


@pytest.mark.parametrize(
    "bad",
    [
        [{"task_id": "made-up", "action": "follow_up", "message": "Add a case"}],
        [
            {"task_id": "TASK", "action": "follow_up", "message": "Add a case"},
            {"task_id": "TASK", "action": "cancel"},
        ],
        [{"task_id": "TASK", "action": "pause", "patch": {"title": "Renamed"}}],
    ],
)
def test_an_unusable_task_change_is_repaired_instead_of_failing_the_reply(
    home: RemHome, tmp_path: Path, bad: list[dict[str, Any]]
):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    wait_run(home, task["id"], {"succeeded"})
    calls: list[str] = []
    good = [{"task_id": task["id"], "action": "follow_up", "message": "Add a case"}]

    def generate(prompt, config, cancel):
        calls.append(prompt)
        controls = (
            good if len(calls) > 1 else json.loads(json.dumps(bad).replace("TASK", task["id"]))
        )
        return {"reply": "I’ll add the case.", "controls": controls}

    home.generate = generate
    home.submit_message({"request_id": "case", "body": "Add a case."})
    home._reply()
    assert len(calls) == 2 and "Repair the decision" in calls[1]
    reply = next(m for m in home.store.conversation()["messages"] if m["id"] == "reply-case")
    assert reply["body"] == "I’ll add the case."
    assert home.store.tasks()[0]["active_run"]


def test_a_reply_that_also_asks_to_read_acts_only_on_the_final_decision(home: RemHome):
    calls: list[str] = []

    def generate(prompt, config, cancel):
        calls.append(prompt)
        if len(calls) == 1:
            return {"recall": {"queries": ["Earlier"]}, "reply": "Done", "tasks": [recipe()]}
        return {"reply": "Starting it.", "tasks": [recipe(title="Final")]}

    home.generate = generate
    home.submit_message({"request_id": "mixed", "body": "Build the thing from earlier"})
    home._reply()
    assert len(calls) == 2
    assert [t["title"] for t in home.store.tasks()] == ["Final"]


def test_a_task_update_that_fails_to_post_waits_before_trying_again(
    home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    home._finish(task["active_run"]["id"], "succeeded", "Finished the feature.", 0)
    original = home._outcome_parts
    attempts = 0

    def broken(*args: Any) -> Any:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("database is locked")

    monkeypatch.setattr(home, "_outcome_parts", broken)
    for _ in range(3):
        home.deliver_outcomes()
    # Writing an update can call the chat model; it isn't retried on every tick.
    assert attempts == 1
    (entry,) = home.store.outbox()
    monkeypatch.setattr(home, "_outcome_parts", original)
    home.delivery_backoff[entry["id"]] = (1, 0.0)
    home.deliver_outcomes()
    assert not home.store.outbox()
    assert entry["id"] not in home.delivery_backoff
    assert any(m["id"] == entry["id"] for m in home.store.conversation()["messages"])


def test_conversation_decision_retries_temporary_provider_errors(
    home: RemHome, instant_retries: None
):
    calls = 0

    def generate(prompt, config, cancel):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("Rate limit reached; try again shortly")
        return {"reply": "Hello again."}

    home.generate = generate
    home.submit_message({"request_id": "hello", "body": "Hi"})
    home._reply()
    assert calls == 2
    assert home.store.conversation()["messages"][-1]["body"] == "Hello again."


def test_conversation_asks_again_when_decision_is_not_json(home: RemHome):
    """The screenshot's "Expecting value: line 1 column 1 (char 0)" reply."""
    prompts: list[str] = []

    def generate(prompt, config, cancel):
        prompts.append(prompt)
        if len(prompts) == 1:
            raise MalformedDecision("I'll look into that report first.")
        return {"reply": "Started the fixes."}

    home.generate = generate
    home.submit_message({"request_id": "fix", "body": "Implement the report's proposals."})
    home._reply()
    assert len(prompts) == 2
    assert "not the requested JSON object" in prompts[1]
    assert home.store.conversation()["messages"][-1]["body"] == "Started the fixes."


def test_unreadable_decision_explains_itself_without_parser_errors(home: RemHome):
    calls = 0

    def generate(prompt, config, cancel):
        nonlocal calls
        calls += 1
        raise json.JSONDecodeError("Expecting value", "", 0)

    home.generate = generate
    home.submit_message({"request_id": "fix", "body": "Implement the report's proposals."})
    home._reply()
    assert calls == 2
    assert not home.store.tasks()
    assert home.store.conversation()["conversation"]["turn_state"] == "idle"
    error = home.store.conversation()["messages"][-1]["body"]
    assert "Expecting value" not in error and "char 0" not in error
    assert "couldn’t read my own reply" in error and "didn’t change anything" in error


def test_a_retried_reply_is_logged_as_routine_and_never_quoted(home: RemHome, caplog):
    secret_words = "my private plans for the weekend"

    def generate(prompt, config, cancel):
        if "not the requested JSON object" not in prompt:
            raise MalformedDecision(f"Sure! {secret_words}")
        return {"reply": "Done."}

    home.generate = generate
    caplog.set_level(logging.DEBUG, logger="gofer")
    home.submit_message({"request_id": "fix", "body": secret_words})
    home._reply()
    records = [r for r in caplog.records if r.name == "gofer.ui.rem_home"]
    assert [(r.levelno, r.getMessage()) for r in records] == [
        (
            logging.INFO,
            f"Rem's reply was not the requested JSON ({len('Sure! ' + secret_words)} characters); "
            "asking again",
        )
    ]
    assert all(secret_words not in r.getMessage() for r in caplog.records)


def test_failed_requests_reach_the_app_log_at_the_right_level(home: RemHome, caplog):
    caplog.set_level(logging.DEBUG, logger="gofer")

    def unreadable(prompt, config, cancel):
        raise MalformedDecision("still chatting, private words")

    home.generate = unreadable
    home.submit_message({"request_id": "garbled", "body": "private words"})
    home._reply()
    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning.getMessage().startswith("Rem couldn't read its own reply to request garbled")
    assert "private words" not in warning.getMessage() and warning.exc_info is None
    caplog.clear()

    def broken(prompt, config, cancel):
        raise RuntimeError("bug in Rem")

    home.generate = broken
    home.submit_message({"request_id": "crash", "body": "Do the thing."})
    home._reply()
    [error] = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error.getMessage() == "Rem failed while answering request crash"
    assert error.exc_info and error.exc_info[0] is RuntimeError, "the traceback is kept"
    assert "bug in Rem" in home.store.conversation()["messages"][-1]["body"]


def test_outcome_update_never_promises_actions_rem_cannot_take(home: RemHome, tmp_path: Path):
    prompts: list[str] = []

    def generate(prompt, config, cancel):
        prompts.append(prompt)
        return {
            "summary": "The work is saved. Next, I'll switch that thread out of plan mode.",
            "needs_user": False,
        }

    home.generate = generate
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    update = next(
        m
        for m in home.store.conversation()["messages"]
        if m["id"].startswith(f"finished-{run['id']}")
    )
    assert update["body"] == "The work is saved."
    assert "cannot change a thread's permission or plan mode" in prompts[-1]


def test_outcome_commits_before_its_chat_summary_and_survives_restart(
    home: RemHome, tmp_path: Path
):
    entered, release = threading.Event(), threading.Event()

    def generate(prompt, config, cancel):
        entered.set()
        assert release.wait(5)
        return {"summary": "The feature works.", "needs_user": False}

    home.generate = generate
    home.create_task({"request_id": "create", **recipe(tmp_path)})
    deadline = time.monotonic() + 5
    while not entered.is_set():
        assert time.monotonic() < deadline
        home.tick()
        time.sleep(0.01)
    # The summary call is still running, but the outcome is already durable.
    run = home.store.tasks()[0]["last_run"]
    assert run["state"] == "succeeded"
    assert [e["run_id"] for e in home.store.outbox()] == [run["id"]]
    assert not any(m["id"].startswith("finished-") for m in home.store.conversation()["messages"])

    # A replacement backend delivers the pending update exactly once.
    replacement = RemHome(
        home.data_dir,
        source=home.source,
        jobs=home.jobs,
        steering=home.steering,
        authorize=lambda c, r: None,
        generate=lambda p, c, e: {"summary": "Delivered after restart.", "needs_user": False},
        start=False,
    )
    try:
        replacement.deliver_outcomes()
    finally:
        replacement.close()
    release.set()
    deadline = time.monotonic() + 5
    while home.delivering:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    finished = [m for m in home.store.conversation()["messages"] if m["id"].startswith("finished-")]
    assert [m["body"] for m in finished] == ["Delivered after restart."]
    assert not home.store.outbox()
    assert home.store.tasks()[0]["last_run"]["summary_short"] == "Delivered after restart."


def test_cleared_history_drops_pending_updates(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    assert home._finish(run["id"], "succeeded", "Done.", epoch=run["epoch"])
    entry = home.store.outbox()[0]
    with home.store.connect() as db:
        db.execute("DELETE FROM outbox")
    home.delivering.add(entry["id"])
    home._deliver(entry)
    assert not any(m["id"] == entry["id"] for m in home.store.conversation()["messages"])


def test_idle_turn_stops_with_a_clear_continuable_blocker(
    home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import gofer.ui.rem_home as rem_home_module

    monkeypatch.setattr(rem_home_module, "TURN_IDLE_SECONDS", 0)

    async def source(**kwargs):
        while not kwargs["cancel_event"].is_set():
            await asyncio.sleep(0.01)
        yield {"type": "stopped"}

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"blocked", "succeeded", "outcome_unknown"})
    assert run["state"] == "blocked"
    assert "no activity" in run["summary_short"]


def test_active_turns_are_not_idle(home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import gofer.ui.rem_home as rem_home_module

    monkeypatch.setattr(rem_home_module, "TURN_IDLE_SECONDS", 0.2)
    original = home.source

    async def source(**kwargs):
        if "independent read-only reviewer" not in kwargs["agent_instructions"]:
            for index in range(8):
                yield {"type": "thought", "text": f"Working {index}"}
                await asyncio.sleep(0.1)
        async for event in original(**kwargs):
            yield event

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"blocked", "succeeded"})
    assert run["state"] == "succeeded", run
    # Batched transcript writes still record every thought.
    thoughts = [
        m["body"]
        for m in home.store.thread(run["thread_id"])["messages"]
        if m.get("kind") == "thought" and m["body"].startswith("Working")
    ]
    assert thoughts == [f"Working {index}" for index in range(8)]


def test_decision_effects_commit_together_or_not_at_all(home: RemHome, tmp_path: Path):
    first = home.create_task({"request_id": "first", **recipe(tmp_path)})["task"]
    second = home.create_task(
        {"request_id": "second", **recipe(tmp_path / "other", title="Other")}
    )["task"]
    home.generate = lambda p, c, e: {
        "reply": "Done.",
        "controls": [
            {"task_id": first["id"], "action": "cancel"},
            # The second task has no interrupted review, so this control fails.
            {"task_id": second["id"], "action": "retry_review"},
        ],
        "tasks": [recipe(tmp_path, title="Brand new")],
        "memories": ["Prefers short replies"],
    }
    home.submit_message({"request_id": "both", "body": "Stop one and recheck the other"})
    home._reply()
    tasks = {t["id"]: t for t in home.store.tasks()}
    assert len(tasks) == 2
    assert tasks[first["id"]]["active_run"]["state"] == "queued"
    with home.store.read() as db:
        assert home.store.setting(db, "memories") == {}
    reply = home.store.conversation()["messages"][-1]
    assert "didn’t change anything" in reply["body"]
    assert home.store.conversation()["conversation"]["turn_state"] == "idle"


def test_follow_ups_keep_the_goal_and_a_bounded_history(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    for index in range(7):
        with home.store.connect() as db:
            current = home.store.get(db, "runs", run["id"])
            current.update(state="blocked", blocked_phase="executing")
            home.store.put(db, "runs", current)
        home.instruct(run["thread_id"], {"request_id": f"follow-{index}", "body": f"Step {index}"})
    queued = home.store.tasks()[0]["active_run"]
    prompt = queued["recipe"]["prompt"]
    assert prompt.startswith("Implement the feature\n\nUser follow-ups, oldest first:")
    assert prompt.count("Step") == 5 and "Step 6" in prompt and "Step 1" not in prompt
    assert queued["recipe"]["base_prompt"] == "Implement the feature"


def test_follow_up_prompt_drops_oldest_entries_to_fit():
    from gofer.ui.rem_home import PROMPT_LIMIT, follow_up_prompt

    base = "Goal"
    prompt = follow_up_prompt(base, ["a" * 60000, "b" * 30000, "c" * 30000])
    assert len(prompt) <= PROMPT_LIMIT
    assert "a" * 100 not in prompt and "c" * 100 in prompt
    with pytest.raises(ValueError, match="too long"):
        follow_up_prompt("x" * PROMPT_LIMIT, ["more"])


def test_follow_up_clears_a_stale_stop_reason(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    with home.store.connect() as db:
        run.update(state="blocked", blocked_phase="executing", stop_reason="Access revoked")
        home.store.put(db, "runs", run)
    home.instruct(run["thread_id"], {"request_id": "follow", "body": "Access is back"})
    queued = home.store.tasks()[0]["active_run"]
    assert queued["stop_reason"] is None
    home.store.mutate_run(queued["id"], {"request_id": "cancel"}, "cancel")
    assert wait_run(home, task["id"], {"cancelled", "blocked"})["state"] == "cancelled"


def test_reads_do_not_hold_the_write_lock(home: RemHome):
    started = time.monotonic()
    with home.store.read() as reader:
        reader.execute("SELECT count(*) FROM runs").fetchone()
        with home.store.connect() as writer:
            home.store.message(writer, "Written during a read", identity="concurrent")
    assert time.monotonic() - started < 5


def test_unknown_run_states_are_rejected(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    with pytest.raises(ValueError, match="Unknown run state"):
        with home.store.connect() as db:
            home.store.put(db, "runs", {**run, "state": "done"})


def test_retitled_duplicate_proposal_continues_the_existing_task(home: RemHome, tmp_path: Path):
    from gofer.ui.rem_home import title_key

    assert title_key("Fix  Theo’s digest!") == title_key("fix theo's digest")
    task = home.create_task(
        {"request_id": "create", **recipe(tmp_path, title="Fix Theo’s digest")}
    )["task"]
    with home.store.connect() as db:
        run = home.store.get(db, "runs", task["active_run"]["id"])
        run.update(state="blocked", blocked_phase="executing")
        home.store.put(db, "runs", run)
    home.generate = lambda p, c, e: {
        "reply": "Starting.",
        "tasks": [recipe(tmp_path, title="fix theo's digest!", prompt="Try again")],
    }
    home.submit_message({"request_id": "again", "body": "Fix Theo's digest"})
    home._reply()
    assert len(home.store.tasks()) == 1
    assert home.store.tasks()[0]["active_run"]["state"] == "queued"


def test_rewriting_a_transcript_entry_keeps_its_place(tmp_path: Path):
    # Workers read this order back as their conversation history.
    store = RemHomeStore(tmp_path)
    store.append_threads(
        "worker",
        [
            {"id": "prompt", "role": "user", "body": "Investigate"},
            {"id": "reply", "role": "assistant", "body": "Report"},
            {"id": "review", "role": "user", "body": "Please address these findings"},
        ],
    )
    store.append_thread("worker", {"id": "prompt", "role": "user", "body": "Investigate Rem"})
    with store.read() as db:
        rows = db.execute(
            "SELECT body FROM thread_messages WHERE thread='worker' ORDER BY seq"
        ).fetchall()
    messages = [json.loads(row[0]) for row in rows]
    assert [m["id"] for m in messages] == ["prompt", "reply", "review"]
    assert messages[0]["body"] == "Investigate Rem"


def test_the_newest_run_stays_last_after_an_older_run_changes(tmp_path: Path):
    store = RemHomeStore(tmp_path)
    with store.connect() as db:
        task = store.add_task(db, Recipe.model_validate(recipe(type="research")))
        older = store.add_run(db, task, "first", {})
        older.update(state="succeeded")
        store.put(db, "runs", older)
        newer = store.add_run(db, task, "second", {})
        newer.update(state="failed")
        store.put(db, "runs", newer)
        # Saving replaces the row, which used to make the older run look newest.
        older["summary_short"] = "Delivered later"
        store.put(db, "runs", older)
        view = store.task_view(db, task)
    assert view["last_run"]["id"] == newer["id"]
    assert [run["id"] for run in view["recent_runs"]] == [newer["id"], older["id"]]


def test_review_awaiting_a_scheduled_run_waits_instead_of_blocking(home: RemHome, tmp_path: Path):
    original = home.source
    reviews = 0
    workers = 0
    awaited = datetime.now(UTC) + timedelta(hours=6)

    async def source(**kwargs):
        nonlocal reviews, workers
        if "independent read-only reviewer" not in kwargs["agent_instructions"]:
            workers += 1
            async for event in original(**kwargs):
                yield event
            return
        reviews += 1
        assert "wait_until" in kwargs["agent_instructions"]
        yield {
            "type": "thought",
            "trace": {
                "id": f"review-{reviews}",
                "category": "shell",
                "phase": "result",
                "status": "completed",
                "exitCode": 0,
                "output": "timer installed",
            },
        }
        verdict: dict[str, Any] = {
            "criteria": [
                {"id": "check", "result": "pass", "evidence": "The digest exists now."}
                if reviews > 1
                else {"id": "check", "result": "unknown", "evidence": "Needs the 03:00 run."}
            ],
            "feedback": "",
            "question_to_rem": "",
        }
        if reviews == 1:
            verdict |= {"wait_until": awaited.isoformat(), "wait_for": "the 03:00 digest run"}
        yield {"type": "final", "message": {"body": json.dumps(verdict)}}

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    deadline = time.monotonic() + 5
    while True:
        home.tick()
        run = home.store.tasks()[0]["last_run"]
        if run["state"] == "queued" and run.get("waiting_for"):
            break
        assert time.monotonic() < deadline, run
        time.sleep(0.01)
    assert run["waiting_for"] == "the 03:00 digest run"
    assert datetime.fromisoformat(run["not_before"]) > awaited
    assert run["repair_count"] == 0 and run["resume_review"] is True
    home.tick()
    run = home.store.tasks()[0]["last_run"]
    assert run["state"] == "queued"
    assert run["waiting_reason"] == "Waiting for the 03:00 digest run before checking again"

    # Once the awaited time passes, only the review runs again.
    home._update(run["id"], not_before=datetime.now(UTC).isoformat())
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert workers == 1 and reviews == 2
    assert run.get("waiting_for") is None


@pytest.mark.parametrize(
    "patch",
    [
        {"wait_until": "not a time"},
        {"wait_until": "2026-01-01T00:00:00"},
        {"wait_for": ""},
        {"result": "fail"},
        {"far": True},
        {"proof_waits": 3},
    ],
)
def test_proof_wait_requires_a_bounded_future_event(patch: dict[str, Any]):
    until = datetime.now(UTC) + (timedelta(days=30) if patch.get("far") else timedelta(hours=1))
    verdict = {
        "criteria": [{"id": "check", "result": patch.get("result", "unknown"), "evidence": "x"}],
        "wait_until": patch.get("wait_until", until.isoformat()),
        "wait_for": patch.get("wait_for", "the next run"),
    }
    run = {"proof_waits": patch.get("proof_waits", 0)}
    assert RemHome._proof_wait(run, verdict) is None
    soon = datetime.now(UTC) + timedelta(hours=1)
    valid = {
        "criteria": [{"id": "check", "result": "unknown", "evidence": "x"}],
        "wait_until": soon.isoformat(),
        "wait_for": "the next run",
    }
    assert RemHome._proof_wait({}, valid) == (soon + timedelta(minutes=30), "the next run")


def test_settings_limit_workers_and_reach_every_thread(home: RemHome, tmp_path: Path):
    from gofer.core.command_slots import read_settings, slots_root

    with pytest.raises(ValueError):
        home.configure({"provider": "codex", "command_slots": {"maxWorkers": 99}})
    home.configure(
        {"provider": "codex", "model": "cli-default", "command_slots": {"maxWorkers": 1, "test": 2}}
    )
    assert read_settings(slots_root(home.data_dir)).as_json() == {
        "enabled": True,
        "maxWorkers": 1,
        "test": 2,
        "build": 0,
        "browser": 0,
    }
    # Callers that leave the field out keep the saved limits.
    home.configure({"provider": "codex", "model": "cli-default"})
    assert read_settings(slots_root(home.data_dir)).max_workers == 1

    entered = threading.Event()
    release = threading.Event()
    original = home.source

    async def source(**kwargs):
        if "independent read-only reviewer" not in kwargs["agent_instructions"]:
            entered.set()
            while not release.is_set() and not kwargs["cancel_event"].is_set():
                await asyncio.sleep(0.01)
        async for event in original(**kwargs):
            yield event

    home.source = source
    first = home.create_task({"request_id": "first", **recipe(tmp_path / "a")})["task"]
    second = home.create_task({"request_id": "second", **recipe(tmp_path / "b")})["task"]
    try:
        home.tick()
        assert entered.wait(2)
        home.tick()
        assert len(home.workers) == 1
        waiting = next(t for t in home.store.tasks() if t["id"] == second["id"])["active_run"]
        assert waiting["waiting_reason"] == "Waiting for a free worker (1 task is running)"
    finally:
        release.set()
    assert wait_run(home, first["id"], {"succeeded", "blocked"})["state"] == "succeeded"
    assert wait_run(home, second["id"], {"succeeded", "blocked"})["state"] == "succeeded"


def test_new_workers_wait_for_free_memory(
    home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import gofer.ui.rem_home as rem_home_module

    available = [8 * 1024**3]
    monkeypatch.setattr(rem_home_module, "memory_available_bytes", lambda: available[0])
    entered = threading.Event()
    release = threading.Event()
    original = home.source

    async def source(**kwargs):
        if "independent read-only reviewer" not in kwargs["agent_instructions"]:
            entered.set()
            while not release.is_set() and not kwargs["cancel_event"].is_set():
                await asyncio.sleep(0.01)
        async for event in original(**kwargs):
            yield event

    home.source = source
    first = home.create_task({"request_id": "first", **recipe(tmp_path / "a")})["task"]
    home.tick()
    assert entered.wait(2)
    available[0] = 1024**3
    second = home.create_task({"request_id": "second", **recipe(tmp_path / "b")})["task"]
    try:
        home.tick()
        waiting = next(t for t in home.store.tasks() if t["id"] == second["id"])["active_run"]
        assert waiting["waiting_reason"] == "Waiting for free memory (1.0 GB available)"
        assert len(home.workers) == 1
    finally:
        available[0] = 8 * 1024**3
        release.set()
    assert wait_run(home, first["id"], {"succeeded", "blocked"})["state"] == "succeeded"
    assert wait_run(home, second["id"], {"succeeded", "blocked"})["state"] == "succeeded"


def test_rem_tests_admit_the_same_workers_on_a_small_computer(
    home: RemHome, monkeypatch: pytest.MonkeyPatch
):
    import gofer.core.command_slots as command_slots
    import gofer.ui.rem_home as rem_home_module

    # A one-core, 2 GB computer would allow one worker automatically.
    monkeypatch.setattr(command_slots, "cpu_count", lambda: 1)
    monkeypatch.setattr(command_slots, "memory_total_bytes", lambda: 2 * 1024**3)
    assert command_slots.default_limits()["maxWorkers"] == 1

    # The fixture's saved limit and free memory keep concurrency tests host-independent.
    assert home._admission() == (8, None)
    available = rem_home_module.memory_available_bytes()
    assert available is not None and available >= command_slots.WORKER_MEMORY_FLOOR


def test_waiting_or_running_tests_keep_a_quiet_turn_alive(
    home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import gofer.ui.rem_home as rem_home_module
    from gofer.core.command_slots import activity_path, heartbeat, slots_root

    monkeypatch.setattr(rem_home_module, "TURN_IDLE_SECONDS", 0.3)
    monkeypatch.setattr("gofer.core.command_slots.HEARTBEAT_SECONDS", 0.05)
    original = home.source

    async def source(**kwargs):
        if "independent read-only reviewer" not in kwargs["agent_instructions"]:
            # A gated command reports progress through the turn's activity file,
            # never through provider events.
            path = activity_path(slots_root(home.data_dir), kwargs["workflow"]["chatThreadId"])
            path.parent.mkdir(parents=True, exist_ok=True)
            with heartbeat(str(path)):
                await asyncio.sleep(1)
        async for event in original(**kwargs):
            yield event

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"blocked", "succeeded"})
    assert run["state"] == "succeeded", run


def test_reply_keeps_details_follow_ups_and_timing(home: RemHome):
    seen: list[dict[str, Any]] = []

    def generate(prompt, config, cancel):
        conversation = home.store.conversation()["conversation"]
        seen.append(dict(conversation))
        if len(seen) == 1:
            return {"recall": {"queries": ["vector"]}}
        # The chat reply streams into the conversation while it is written.
        home._drafting.sink("Short ans")
        seen.append(dict(home.store.conversation()["conversation"]))
        return {
            "reply": "Short answer.",
            "details": "| Option | Score |\n|---|---|\n| A | 1 |",
            "suggestions": ["Why A?", "why a?", " ", "Compare B", "Set it up", "Fourth"],
            "tasks": [],
            "memories": [],
        }

    home.generate = generate
    home.submit_message({"request_id": "rich-reply", "body": "Which option?"})
    home._reply()
    assert seen[0]["turn_label"] == "Reading your message"
    assert seen[1]["turn_label"] == "Reading earlier messages"
    assert seen[2]["draft_reply"] == "Short ans"
    reply = next(m for m in home.store.conversation()["messages"] if m["id"] == "reply-rich-reply")
    assert reply["details"].startswith("| Option | Score |\n|---|---|")
    assert reply["suggestions"] == ["Why A?", "Compare B", "Set it up"]
    assert reply["meta"]["provider"] == "codex" and reply["meta"]["seconds"] >= 0
    conversation = home.store.conversation()["conversation"]
    assert conversation["turn_label"] is None and conversation["draft_reply"] is None
    assert getattr(home._drafting, "sink", None) is None
    # The chat prompt asks for the optional depth and follow-ups.
    assert "details" in home.store.conversation()["messages"][-1]


def test_task_update_carries_a_result_card(home: RemHome, tmp_path: Path):
    from gofer.ui.rem_home_results import result_card

    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    report = tmp_path / "vector-report.html"
    report.write_text("<html><head><title>Vector &amp; search</title></head></html>")
    with home.store.connect() as db:
        current = home.store.get(db, "runs", run["id"])
        current["worker_result"] = "Saved it. See [FTS5](https://www.sqlite.org/fts5.html)."
        current["criteria"] = [{"id": "a", "result": "pass"}, {"id": "b", "result": "pass"}]
        current["artifacts"] = [
            {"path": str(report), "link": f"[vector-report](<{report}>)", "epoch": 0},
            {"path": str(tmp_path / "gone.html"), "link": "[gone](</x>)", "epoch": 0},
        ]
        home.store.put(db, "runs", current)
    home.store.append_thread(
        run["thread_id"],
        {
            "id": "search-1",
            "role": "assistant",
            "worker_role": "execute",
            "kind": "thought",
            "body": "",
            "trace": {
                "kind": "tool",
                "title": "WebSearch",
                "input": json.dumps({"query": "sqlite vector"}),
                "output": 'Links: [{"title":"LanceDB","url":"https://lancedb.com/"},'
                '{"title":"Local","url":"http://192.168.1.2/admin"}]',
            },
        },
    )
    home.generate = lambda *args: {
        "summary": "The comparison is ready.",
        "details": "- SQLite is enough\n- I'll check again tomorrow",
        "suggestions": ["Set up SQLite"],
        "needs_user": False,
    }
    assert home._finish(run["id"], "succeeded", "Done.", epoch=run["epoch"])
    home.deliver_outcomes()
    update = next(
        m for m in home.store.conversation()["messages"] if m["id"].startswith("finished-")
    )
    assert update["body"].startswith("The comparison is ready.")
    assert f"[vector-report](<{report}>)" in update["body"]
    # Promises nobody keeps are dropped line by line; the list stays a list.
    assert update["details"] == "- SQLite is enough"
    assert update["suggestions"] == ["Set up SQLite"]
    card = update["result"]
    assert card["checks"] == {"passed": 2, "failed": 0, "total": 2}
    assert [a["title"] for a in card["artifacts"]] == ["Vector & search"]
    assert card["artifacts"][0]["kind"] == "html" and card["artifacts"][0]["size"] > 0
    assert [s["url"] for s in card["sources"]] == [
        "https://www.sqlite.org/fts5.html",
        "https://lancedb.com/",
    ]
    assert card["sources"][0]["kind"] == "cited" and card["thread_id"] == run["thread_id"]
    # Without checks, files or sources there is nothing for a card to show.
    empty = result_card({"recipe": {"title": "T"}, "criteria": []}, "succeeded", [])
    assert not (empty["checks"]["total"] or empty["artifacts"] or empty["sources"])


def test_turn_sources_need_web_use_and_skip_private_addresses():
    from gofer.ui.rem_home_results import public_url, turn_sources

    shell = {"trace": {"kind": "tool", "title": "Bash", "output": "https://example.com"}}
    assert turn_sources([shell], "See https://example.com") == []
    fetch = {
        "worker_role": "execute",
        "trace": {
            "kind": "tool",
            "title": "WebFetch",
            "input": json.dumps({"url": "https://docs.example.com/guide#part"}),
        },
    }
    review = {**fetch, "worker_role": "verify"}
    sources = turn_sources(
        [fetch, review], "Read https://docs.example.com/guide/. Also [A](https://a.example)."
    )
    assert [(s["url"], s["kind"]) for s in sources] == [
        ("https://docs.example.com/guide#part", "cited"),
        ("https://a.example", "cited"),
    ]
    for private in (
        "http://localhost:3000",
        "http://10.0.0.2/x",
        "https://printer.local",
        "ftp://x.com",
    ):
        assert public_url(private) == ""
    assert public_url("https://example.com:8443/") == ""


def test_draft_reply_reads_the_reply_string_as_it_arrives():
    from gofer.ui.rem_home_provider import _RemClaude, draft_reply

    assert draft_reply('{"reply":"Hel') == "Hel"
    assert draft_reply('{"reply": "Line\\nTwo \\"q\\" \\u00e9 \\') == 'Line\nTwo "q" é '
    assert draft_reply('{"reply":"Done.","tasks":[]}') == "Done."
    assert draft_reply('{"recall":{"queries":["x"]}}') == ""
    assert draft_reply('{"reply":"half \\u00') == "half "
    seen: list[str] = []
    claude = _RemClaude(seen.append)
    delta = {
        "type": "stream_event",
        "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": '{"re'}},
    }
    line = json.dumps(delta)
    message, _ = claude._parse_provider_output(line, "")
    assert seen == ['{"re'] and message == line
    final, _ = claude._parse_provider_output(json.dumps({"type": "result", "result": "{}"}), "")
    assert final == "{}" and seen == ['{"re']


def test_rem_attaches_any_readable_file_the_user_asked_for(
    home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import gofer.ui.rem_home as rem_home_module

    house = tmp_path / "home"
    monkeypatch.setattr(rem_home_module.Path, "home", classmethod(lambda cls: house))
    pictures = house / "Pictures" / "shots"
    pictures.mkdir(parents=True)
    shot = pictures / "desktop light.png"
    shot.write_bytes(b"\x89PNG fake")
    keys = house / ".ssh"
    keys.mkdir()
    (keys / "id_ed25519").write_text("PRIVATE")
    unnamed = pictures / "other.png"
    unnamed.write_bytes(b"x")
    attached = home.data_dir / "chat-attachments" / "rem-home" / "a.txt"
    attached.parent.mkdir(parents=True, exist_ok=True)
    attached.write_text("mine")
    own = home.data_dir / "rem-home.sqlite3"
    named = [str(shot), str(keys / "id_ed25519"), str(unnamed), str(attached), str(own)]
    home.generate = lambda p, c, e: {"reply": "Here they are.", "files": named}
    # The request names the screenshot, the key, the attachment and Raticode's database;
    # other.png is never mentioned.
    home.submit_message(
        {
            "request_id": "any-file",
            "body": f"Send {shot}, {attached} and {own}. Also {keys / 'id_ed25519'}?",
        }
    )
    home._reply()
    reply = home.store.conversation()["messages"][-1]
    sent = {f["name"]: f for f in reply["files"]}
    # Outside open projects and the Second Brain, by exact path from the conversation.
    assert sent["desktop light.png"]["path"] == str(shot.resolve())
    assert sent["a.txt"]["size"] == 4
    # A credential file is sent only because this very message names its path.
    assert "id_ed25519" in sent
    assert "other.png: its path isn’t in our conversation" in reply["body"]
    assert reply["reply_to"] == "any-file"

    # The same key and Raticode's own data, named only earlier, are not sent again.
    home.generate = lambda p, c, e: {
        "reply": "Again.",
        "files": [str(keys / "id_ed25519"), str(own)],
    }
    home.submit_message({"request_id": "again", "body": "Send those two again"})
    home._reply()
    again = home.store.conversation()["messages"][-1]
    assert "files" not in again
    assert "id_ed25519: it may hold passwords" in again["body"]
    assert "rem-home.sqlite3: it may hold passwords" in again["body"]


def test_reply_files_rules_for_files_outside_open_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import gofer.ui.rem_home as rem_home_module
    from gofer.ui.rem_home import reply_files, sensitive_path

    monkeypatch.setattr(rem_home_module.Path, "home", classmethod(lambda cls: tmp_path))
    shot = tmp_path / "Pictures" / "shot.png"
    shot.parent.mkdir()
    shot.write_bytes(b"png")
    # Without conversation records, only open folders count, as before.
    files, refused = reply_files([str(shot)], {})
    assert not files and "isn’t in an open project" in refused[0]
    files, refused = reply_files([str(shot)], {}, records=f"see {shot}")
    assert [f["name"] for f in files] == ["shot.png"] and not refused
    unreadable = tmp_path / "Pictures" / "locked.png"
    unreadable.write_bytes(b"x")
    unreadable.chmod(0)
    try:
        if not os.access(unreadable, os.R_OK):
            files, refused = reply_files([str(unreadable)], {}, records=str(unreadable))
            assert "this account can’t read it" in refused[0]
    finally:
        unreadable.chmod(0o600)
    assert sensitive_path(Path("/proc/self/environ"))
    assert sensitive_path(tmp_path / ".aws" / "credentials")
    assert sensitive_path(tmp_path / "project" / ".env.local")
    assert not sensitive_path(shot)


def test_files_listed_by_folder_and_name_can_be_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import gofer.ui.rem_home as rem_home_module
    from gofer.ui.rem_home import reply_files

    monkeypatch.setattr(rem_home_module.Path, "home", classmethod(lambda cls: tmp_path))
    folder = tmp_path / "Raticode Mobile" / "screenshots" / "voice-synth"
    folder.mkdir(parents=True)
    for name in ("small-320dp-paused.png", "small-320dp-review.png", "other.png"):
        (folder / name).write_bytes(b"png")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "plan.png").write_bytes(b"png")
    (folder / "plan.png").write_bytes(b"png")
    (folder / ".env").write_text("API_KEY=synthetic-not-a-real-key")
    # How the voice-mode worker reported its screenshots: the folder once, then names.
    records = (
        f"Screenshots are in `{folder}/`:\n- `small-320dp-paused.png`\n"
        "- `small-320dp-review.png`\n- `.env`\n"
        f"Plan: `{elsewhere / 'plan.png'}`\n"
    )
    asked = [str(folder / n) for n in ("small-320dp-paused.png", "small-320dp-review.png")]
    files, refused = reply_files(asked, {}, records=records)
    assert [f["name"] for f in files] == ["small-320dp-paused.png", "small-320dp-review.png"]
    assert not refused
    # The ~/ form of the folder counts too.
    home_form = records.replace(str(tmp_path), "~")
    files, _ = reply_files(asked[:1], {}, records=home_form)
    assert [f["name"] for f in files] == ["small-320dp-paused.png"]

    def refusal(path: Path, text: str) -> str:
        files, refused = reply_files([str(path)], {}, records=text)
        assert not files
        return refused[0]

    # A name the records never list in that folder is still a guess.
    assert "isn’t in our conversation" in refusal(folder / "other.png", records)
    # A name that appears only inside another folder's path doesn't pair with this one.
    assert "isn’t in our conversation" in refusal(folder / "plan.png", records)
    # Part of a longer name doesn't count, and neither does a folder that only prefixes one.
    assert "isn’t in our conversation" in refusal(
        folder / "small-320dp-paused.png", f"In `{folder}/`: `big-small-320dp-paused.png`"
    )
    assert "isn’t in our conversation" in refusal(
        folder / "small-320dp-paused.png", f"In `{folder}-old/`: `small-320dp-paused.png`"
    )
    # Secret-looking names still need the request to name the exact path.
    assert "may hold passwords" in refusal(folder / ".env", records)


def test_reply_whose_files_were_all_refused_says_so_instead(home: RemHome, tmp_path: Path):
    missing = tmp_path / "gone.png"
    home.generate = lambda p, c, e: {"reply": "Here are the screenshots.", "files": [str(missing)]}
    home.submit_message({"request_id": "shots", "body": "Send me the screenshots"})
    home._reply()
    reply = home.store.conversation()["messages"][-1]
    assert reply["body"] == "I couldn’t attach gone.png: that file doesn’t exist."
    assert "files" not in reply

    # When some files arrive, Rem's own words stay above the refusals.
    shot = tmp_path / "project" / "shot.png"
    shot.parent.mkdir()
    shot.write_bytes(b"png")
    home.configure(
        {"provider": "codex", "model": "cli-default", "projects": [{"root": str(shot.parent)}]}
    )
    home.generate = lambda p, c, e: {
        "reply": "Here are the screenshots.",
        "files": [str(shot), str(missing)],
    }
    home.submit_message({"request_id": "some", "body": "Send me the screenshots"})
    home._reply()
    reply = home.store.conversation()["messages"][-1]
    assert reply["body"].startswith("Here are the screenshots.\n\nI couldn’t attach gone.png")
    assert [f["name"] for f in reply["files"]] == ["shot.png"]


def test_agent_sign_ins_and_secret_files_need_the_request_to_name_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import gofer.ui.rem_home as rem_home_module
    from gofer.ui.rem_home import reply_files, sensitive_path

    house = tmp_path / "home"
    monkeypatch.setattr(rem_home_module.Path, "home", classmethod(lambda cls: house))
    codex = house / ".codex" / "auth.json"
    claude = house / ".claude" / ".credentials.json"
    for secret in (codex, claude):
        secret.parent.mkdir(parents=True)
        secret.write_text('{"token": "x"}')
    # Recalled worker notes mention both files; the user only says "send them".
    records = f"Worker notes: checked {codex} and {claude}."
    files, refused = reply_files(
        [str(codex), str(claude)], {}, records=records, request="send them"
    )
    assert files == []
    assert all("may hold passwords, keys" in line for line in refused) and len(refused) == 2
    # Named in the user's current message, each one is sent.
    files, refused = reply_files(
        [str(codex), str(claude)], {}, records=records, request=f"send {codex} and {claude}"
    )
    assert [f["name"] for f in files] == ["auth.json", ".credentials.json"] and not refused

    for name in (
        "auth.json",
        "gcp-credentials.json",
        "client_secret.json",
        "tokens.json",
        "cert.p12",
        "store.pfx",
        "id_ed25519",
        "id_rsa.pub",
        ".env.production",
        "server.key",
        # Sign-in files tools also keep inside projects (2026-11-07 audit).
        ".npmrc",
        ".netrc",
        "_netrc",
        ".pypirc",
        ".pgpass",
        ".dockercfg",
        "kubeconfig",
        "terraform.tfstate",
        "terraform.tfstate.backup",
    ):
        assert sensitive_path(tmp_path / "anywhere" / name), name
    for folder, name in ((".git", "config"), (".docker", "config.json"), (".kube", "config")):
        assert sensitive_path(tmp_path / "project" / folder / name), f"{folder}/{name}"
    for name in ("desktop-light-result-card.png", "identity.md", "notes.txt", "author.json"):
        assert not sensitive_path(tmp_path / "anywhere" / name), name
    # The same generic names elsewhere stay ordinary files.
    for rel in ("app/config", "web/config.json", "docs/git-config.md", ".github/config.yml"):
        assert not sensitive_path(tmp_path / "project" / rel), rel
    for folder in (".config/claude", ".config/github-copilot", ".gemini", ".cursor"):
        assert sensitive_path(house / folder / "settings.json"), folder
    # Browser profiles and credential stores on Linux, macOS and Windows (2026-11-10 audit).
    for folder in (
        ".config/microsoft-edge/Default",
        "Library/Keychains",
        "Library/Application Support/Google/Chrome/Default",
        "Library/Application Support/Microsoft Edge/Default",
        "Library/Application Support/Firefox/Profiles/x.default",
        "AppData/Local/Google/Chrome/User Data/Default",
        "AppData/Local/Microsoft/Edge/User Data/Default",
        "AppData/Roaming/Mozilla/Firefox/Profiles/x.default",
        "AppData/Roaming/Microsoft/Protect",
    ):
        assert sensitive_path(house / folder / "History"), folder
    assert not sensitive_path(house / "Library" / "Mobile Documents" / "notes.txt")

    # Raticode's data root, marked by its trusted tools folder, is protected around data_dir.
    root = tmp_path / "state" / "data"
    data = root / "gofer"
    (root / ".gofer-trusted-bin").mkdir(parents=True)
    (data / "chat-attachments").mkdir(parents=True)
    assert sensitive_path(root / ".gofer-trusted-bin" / "Raticode", data)
    assert sensitive_path(data / "rem-home.sqlite3", data)
    assert not sensitive_path(data / "chat-attachments" / "photo.jpg", data)
    # An ordinary folder holding data_dir isn't treated as Raticode's own.
    plain = tmp_path / "Projects" / "Workflows"
    plain.mkdir(parents=True)
    assert not sensitive_path(tmp_path / "Projects" / "report.pdf", plain)
    assert sensitive_path(plain / "registry.sqlite3", plain)


def test_secret_files_inside_open_folders_need_the_request_to_name_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import gofer.ui.rem_home as rem_home_module
    from gofer.ui.rem_home import names_path, reply_files

    house = tmp_path / "home"
    monkeypatch.setattr(rem_home_module.Path, "home", classmethod(lambda cls: house))
    project = house / "Projects" / "app"
    brain = house / "brain"
    for folder in (project / "src", brain, house / ".ssh"):
        folder.mkdir(parents=True)
    # Synthetic credentials only.
    env = project / ".env"
    env.write_text("API_KEY=synthetic-not-a-real-key\n")
    secret = project / "secret.txt"
    secret.write_text("synthetic")
    token = brain / "github-token.txt"
    token.write_text("synthetic")
    key = house / ".ssh" / "id_ed25519"
    key.write_text("synthetic private key")
    (house / ".ssh" / "id_ed25519.pub").write_text("synthetic public key")
    code = project / "src" / "app.py"
    code.write_text("print('hi')")
    config = {
        "projects": [{"root": str(project)}, {"root": str(house)}],
        "brain": {"root": str(brain)},
    }
    paths = [str(env), str(secret), str(token), str(key), str(code)]

    # Open folders, even the home folder, no longer release secret-looking files or
    # credential stores to a reply the user didn't ask for by path.
    files, refused = reply_files(paths, config, records="", request="send the project files")
    assert [f["name"] for f in files] == ["app.py"]
    assert len(refused) == 4 and all("may hold passwords, keys" in line for line in refused)
    # Without records, as before, open folders still need no conversation mention.
    files, refused = reply_files([str(code)], config)
    assert [f["name"] for f in files] == ["app.py"] and not refused

    # Naming the exact path, or its ~/ form, releases it.
    files, refused = reply_files(
        [str(env), str(key)], config, request=f"send {env} and ~/.ssh/id_ed25519."
    )
    assert [f["name"] for f in files] == [".env", "id_ed25519"] and not refused
    # A longer path that starts with it doesn't: asking for the public key isn't
    # asking for the private one.
    files, refused = reply_files([str(key)], config, request=f"send {key}.pub")
    assert files == [] and "may hold passwords, keys" in refused[0]
    assert not names_path(f"/tmp{key}", str(key))
    assert names_path(f"please send `{key}`, thanks", str(key))
    assert not names_path(f"{key}-backup", str(key))


def test_folders_opened_inside_protected_places_stay_shareable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import gofer.ui.rem_home as rem_home_module
    from gofer.ui.rem_home import reply_files, sensitive_path

    house = tmp_path / "home"
    monkeypatch.setattr(rem_home_module.Path, "home", classmethod(lambda cls: house))
    # A Second Brain kept in Raticode's data folder, and a skill folder opened as a project.
    data = tmp_path / "data" / "gofer"
    brain = data / "second-brains" / "notes"
    skill = house / ".codex" / "skills" / "design"
    for folder in (brain, skill):
        folder.mkdir(parents=True)
    note = brain / "plan.md"
    note.write_text("plan")
    guide = skill / "SKILL.md"
    guide.write_text("skill")
    sign_in = house / ".codex" / "config.toml"
    sign_in.write_text("synthetic")
    config = {"projects": [{"root": str(skill)}], "brain": {"root": str(brain)}}
    files, refused = reply_files(
        [str(note), str(guide)], config, records="", request="send them", data_dir=data
    )
    assert [f["name"] for f in files] == ["plan.md", "SKILL.md"] and not refused
    # Only the opened folder is released: a broader open folder holding the agent's
    # sign-in folder or Raticode's data doesn't release them.
    assert not sensitive_path(guide, opened=skill.resolve())
    assert sensitive_path(sign_in, opened=house.resolve())
    assert sensitive_path(data / "rem-home.sqlite3", data, opened=tmp_path.resolve())


def test_new_workers_wait_while_swap_is_nearly_full(
    home: RemHome, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gofer.core.command_slots as command_slots
    import gofer.ui.rem_home as rem_home_module

    monkeypatch.setattr(rem_home_module, "swap_blocked", command_slots.swap_blocked)
    monkeypatch.setattr(command_slots, "swap_used_share", lambda: 0.9)
    limit, reason = home._admission()
    # One worker can always start, whatever the swap.
    assert reason is None
    finished = threading.Thread(target=lambda: None)
    finished.start()
    finished.join()
    monkeypatch.setattr(home, "workers", {"busy": (finished, threading.Event())})
    assert home._admission() == (limit, "Waiting for free memory (swap is 90% full)")
    monkeypatch.setattr(command_slots, "swap_used_share", lambda: 0.4)
    assert home._admission() == (limit, None)


def test_a_run_stopped_mid_turn_keeps_its_reason_instead_of_a_steering_error(
    home: RemHome, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """A run stopped while its worker streams ends blocked with the real reason.

    Stopping a turn makes the steering stream forget it and then yield a final
    "stopped" event. Rem used to stop the turn again on that event, which raised
    "The requested Rem turn is no longer active", logged it as a worker failure
    and recorded it in the thread instead of the reason the run stopped.
    """
    revoked = threading.Event()
    working = threading.Event()

    def authorize(config: dict[str, Any], task: dict[str, Any]) -> None:
        if revoked.is_set():
            raise ValueError("Rem projects must be canonical existing directories")

    async def source(**kwargs: Any):
        yield {"type": "thought", "text": "Working"}
        working.set()
        while not kwargs["cancel_event"].is_set():
            await asyncio.sleep(0.01)
        yield {"type": "stopped"}

    home.authorize = authorize
    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    deadline = time.monotonic() + 5
    while not working.is_set():
        assert time.monotonic() < deadline
        home.tick()
        time.sleep(0.01)
    revoked.set()
    with caplog.at_level(logging.INFO, logger="gofer.ui.rem_home"):
        run = wait_run(home, task["id"], {"blocked", "cancelled", "succeeded", "outcome_unknown"})
    assert run["state"] == "blocked", run
    assert "canonical existing directories" in run["summary_short"]
    messages = home.store.thread(run["thread_id"])["messages"]
    assert not any("no longer active" in str(m.get("body")) for m in messages), messages
    assert [m["body"] for m in messages if m.get("kind") in {"error", "stopped"}] == ["Stopped"]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], caplog.text


def _worker_prompts(calls: list[dict[str, Any]]) -> list[list[tuple[str, str]]]:
    """Each worker turn's user-role messages as (role, body), oldest first."""
    return [
        [(m["role"], m["body"]) for m in call["messages"] if m["role"] == "user"]
        for call in calls
        if "independent read-only reviewer" not in call["agent_instructions"]
    ]


def _recording(home: RemHome) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    original = home.source

    async def source(**kwargs: Any):
        calls.append({"messages": list(kwargs["messages"]), **kwargs})
        async for event in original(**kwargs):
            yield event

    home.source = source
    return calls


@pytest.mark.parametrize("ending", ["succeeded", "blocked"])
def test_user_prompt_to_an_ended_thread_is_the_turn_prompt_without_a_rem_message(
    home: RemHome, tmp_path: Path, ending: str
):
    """Prompting an ended or errored worker thread adds no Rem continuation message."""
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"succeeded"})
    if ending == "blocked":
        home._finish(run["id"], "blocked", "The worker hit an error", run["epoch"])
    calls = _recording(home)
    home.instruct(run["thread_id"], {"request_id": "mine", "body": "Also cover the empty case"})
    finished = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert finished["state"] == "succeeded", finished
    messages = home.store.thread(finished["thread_id"])["messages"]
    users = [m for m in messages if m["role"] == "user" and m.get("worker_role") != "verify"]
    # One Rem prompt started the task; the user's message is the only one after it.
    assert [(m["sender"], m["body"]) for m in users] == [
        ("rem", "Implement the feature"),
        ("user", "Also cover the empty case"),
    ]
    assert not any("Please continue" in m["body"] for m in messages)
    # The provider sees both authors as user-role messages, labelled, with the
    # user's message last so it answers that.
    turns = _worker_prompts(calls)
    assert turns[0][-1] == ("user", "User: Also cover the empty case")
    assert ("user", "Rem: Implement the feature") in turns[0]
    assert finished.get("user_prompts") == []


def test_user_message_while_a_worker_runs_restarts_it_without_a_rem_message(
    home: RemHome, tmp_path: Path
):
    """Steering a running worker: the restarted turn answers the user's own message."""
    working = threading.Event()
    calls: list[dict[str, Any]] = []
    original = home.source

    async def source(**kwargs: Any):
        calls.append({"messages": list(kwargs["messages"]), **kwargs})
        reviewer = "independent read-only reviewer" in kwargs["agent_instructions"]
        if not reviewer and len(_worker_prompts(calls)) == 1:
            yield {"type": "thought", "text": "Reading the code"}
            working.set()
            while not kwargs["cancel_event"].is_set():
                await asyncio.sleep(0.01)
            yield {"type": "stopped"}
            return
        async for event in original(**kwargs):
            yield event

    home.source = source
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    deadline = time.monotonic() + 5
    while not working.is_set():
        assert time.monotonic() < deadline
        home.tick()
        time.sleep(0.01)
    run = home.store.tasks()[0]["active_run"]
    home.instruct(run["thread_id"], {"request_id": "steer", "body": "Use the new parser instead"})
    # The user's message shows in the thread at once.
    assert home.store.thread(run["thread_id"])["messages"][-1]["id"] == "steer"
    finished = wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=10)
    assert finished["state"] == "succeeded", finished
    messages = home.store.thread(finished["thread_id"])["messages"]
    assert [
        (m["sender"], m["body"])
        for m in messages
        if m["role"] == "user" and m.get("worker_role") != "verify"
    ] == [
        ("rem", "Implement the feature"),
        ("user", "Use the new parser instead"),
    ]
    # The stopped turn's end follows the user's message in the thread, but the
    # provider's copy puts the user's message last.
    ids = [m["id"] for m in messages]
    stopped = next(m["id"] for m in messages if m.get("kind") == "stopped")
    assert ids.index("steer") < ids.index(stopped)
    workers = [c for c in calls if "independent read-only reviewer" not in c["agent_instructions"]]
    second = workers[1]
    assert second["messages"][-1]["body"] == "User: Use the new parser instead"


def test_rem_follow_ups_stay_rem_messages_after_a_pending_user_prompt(
    home: RemHome, tmp_path: Path
):
    """Rem's own follow-up is still its message to the worker, and comes last."""
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"succeeded"})
    home._finish(run["id"], "blocked", "Needs an answer", run["epoch"])
    with home.store.connect() as db:
        home.instruct(
            run["thread_id"], {"request_id": "user", "body": "Mine first"}, notify=False, db=db
        )
        home.instruct(
            run["thread_id"],
            {"request_id": "rem", "body": "Use the connected account."},
            notify=False,
            context={"source_message_id": "chat-1"},
            db=db,
        )
    calls = _recording(home)
    finished = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert finished["state"] == "succeeded", finished
    users = [
        m
        for m in home.store.thread(run["thread_id"])["messages"]
        if m["role"] == "user" and m.get("worker_role") != "verify"
    ]
    assert [(m["sender"], m["body"]) for m in users] == [
        ("rem", "Implement the feature"),
        ("user", "Mine first"),
        ("rem", "Use the connected account."),
    ]
    assert _worker_prompts(calls)[0][-1] == ("user", "Rem: Use the connected account.")


def test_thread_readers_name_the_author_of_older_user_messages(home: RemHome, tmp_path: Path):
    """Messages saved before senders were recorded read as the user's."""
    from gofer.ui.rem_home_recall import RemRecall, ThreadRead

    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"succeeded"})
    home.store.append_thread(
        run["thread_id"], {"id": "legacy", "role": "user", "body": "An old message of mine"}
    )
    with home.store.connect() as db:
        home.store.message(db, "Done.", work=home.store.work(run))
    thread = home.store.thread(run["thread_id"])["messages"]
    assert {m["id"]: m.get("sender") for m in thread if m["role"] == "user"}["legacy"] == "user"
    assert home.store.transcript(run["thread_id"])[-1]["sender"] == "user"
    recall = RemRecall(home.store, {"projects": [{"root": str(tmp_path)}]})
    recalled = recall.read_thread(ThreadRead(thread_id=run["thread_id"]), "")
    senders = {m["id"]: m.get("sender") for m in recalled["messages"] if m["role"] == "user"}
    assert senders["legacy"] == "user"
    assert "rem" in senders.values()
