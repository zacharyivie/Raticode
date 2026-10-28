from __future__ import annotations

import asyncio
import json
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
from gofer.ui.rem_home_models import Recipe, RemConflict, Schedule, completion_recap, worker_config
from gofer.ui.rem_home_store import RemHomeStore


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
def home(tmp_path: Path):
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

    data = tmp_path.parent / (tmp_path.name + "-data")
    instance = RemHome(
        data,
        source=source,
        jobs=ChatJobs(data),
        steering=ChatSteering(data),
        authorize=lambda c, r: None,
        generate=lambda p, c, e: {"reply": "Hello.", "tasks": [], "memories": []},
        start=False,
    )
    instance.configure({"provider": "codex", "model": "cli-default"})
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
        assert metadata["archived"]
    assert home.store.conversation()["conversation"]["model"] == "rem-model"
    assert home.store.conversation()["messages"][-1]["body"] == "Implemented and tested."


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
    assert after[:-1] == before
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
        assert thread["metadata"]["archived"] is True
        assert thread["metadata"]["archivedAt"]
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
    assert thread["metadata"]["archived"] is True
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
    assert thread["metadata"]["archived"] is True
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
        assert home.store.thread(run["thread_id"])["metadata"]["archived"] is True


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
    assert home.store.thread(run["thread_id"])["metadata"]["archived"] is True
    assert not any(m["body"] == "Old answer." for m in home.store.conversation()["messages"])


def test_completion_archives_all_review_attempts_and_backfills_existing_runs(
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
    assert home.store.thread(extra)["metadata"]["archived"] is True
    with home.store.connect() as db:
        thread = home.store.get(db, "threads", finished["thread_id"])
        thread.pop("archived")
        home.store.put(db, "threads", thread)
    restored = RemHomeStore(home.data_dir)
    assert restored.thread(finished["thread_id"])["metadata"]["archived"] is True
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
        assert observed[1]["permission_mode"] == "plan"


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
    ]:
        with pytest.raises(ValueError):
            RemHome._verdict(json.dumps({"criteria": values}), item)


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
    assert run["repair_count"] == 2
    assert "No saved research" in run["summary_short"]


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
    assert home.store.thread(run["thread_id"])["metadata"]["archived"]
    later = home.edit_task(task["id"], {"request_id": "run-again"}, "run")["run"]
    assert home._work_root(later) == home._work_root(run)
    assert home._delegation_context(later, {})


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
    assert len(calls) == (3 if mode == "exhausted" else 1)


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
    assert home.store.thread(blocked["thread_id"])["metadata"]["archived"]


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


def test_stale_outcome_delivered_late_does_not_follow_the_newer_one(
    home: RemHome, tmp_path: Path
):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    home._update(run["id"], state="verifying", worker_result="Implemented and tested.")
    home._finish(run["id"], "blocked", "The project changed during review.", 0)
    # Recovery resumes the review under a new epoch.
    home._update(run["id"], state="verifying", epoch=1)
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
