"""ReplyEffects: how a final chat decision turns into follow-ups, controls and new tasks."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from typing import Any

import pytest

from gofer.ui.rem_home_models import Decision, Recipe, TaskControl
from gofer.ui.rem_home_store import RemHomeStore
from gofer.ui.rem_reply_effects import CONTINUATION, ReplyEffects, after_reference


class FakeStore:
    """The store calls ReplyEffects makes, recorded instead of written."""

    interrupted_review = staticmethod(RemHomeStore.interrupted_review)

    def __init__(self, latest: str | None = None) -> None:
        self.latest = latest
        self.added: list[Recipe] = []
        self.messages: list[str] = []

    def latest_card_task(self, tasks: Any) -> str | None:
        return self.latest if self.latest in tasks else None

    def add_task(self, db: Any, recipe: Recipe, context: Any = None) -> dict[str, Any]:
        self.added.append(recipe)
        return {"id": f"created-{len(self.added) - 1}", "title": recipe.title}

    def starts_now(self, recipe: Recipe) -> bool:
        return False

    def message(self, db: Any, body: str, **extra: Any) -> None:
        self.messages.append(body)


def task(task_id: str, title: str, run: dict[str, Any] | None, **fields: Any) -> dict[str, Any]:
    return {"id": task_id, "title": title, "project_dir": None, "last_run": run, **fields}


def recipe(title: str, **fields: Any) -> Recipe:
    return Recipe(title=title, type="errand", prompt=f"Do {title}", **fields)


def effects(
    decision: Decision, known: dict[str, dict[str, Any]], store: FakeStore | None = None
) -> ReplyEffects:
    home = SimpleNamespace(store=store or FakeStore())
    return ReplyEffects(home, decision, known, "m1", {}, 0.0, False)  # type: ignore[arg-type]


def test_repeated_one_time_task_with_a_finished_run_becomes_a_follow_up_that_keeps_send_files():
    known = {"t1": task("t1", "Fix Theo’s digest", {"id": "r1", "state": "blocked"})}
    decision = Decision(reply="On it.", tasks=[recipe("fix theo's digest!", send_files=True)])
    reply = effects(decision, known)

    reply.fold_repeats()

    # The same Decision _reply holds, changed in place.
    assert reply.decision is decision
    assert decision.tasks == []
    assert decision.reply == "I’ll continue in the existing thread."
    [control] = decision.controls
    assert (control.task_id, control.action) == ("t1", "follow_up")
    assert control.message == "Do fix theo's digest!"
    assert control.patch == {"send_files": True}
    assert reply.followed == {0: "t1"}
    assert reply.followed_run is None


@pytest.mark.parametrize("state", ["executing", "outcome_unknown"])
def test_active_or_unknown_run_is_followed_without_a_control(state: str):
    run = {"id": "r1", "state": state}
    known = {"t1": task("t1", "Digest", None, active_run=run)}
    decision = Decision(reply="On it.", tasks=[recipe("Digest")])
    reply = effects(decision, known)

    reply.fold_repeats()

    assert decision.controls == []
    assert decision.tasks == []
    assert reply.followed_run is run


def test_interrupted_review_becomes_retry_review():
    run = {
        "id": "r1",
        "state": "blocked",
        "worker_result": "done",
        "verification_thread_id": "v1",
    }
    known = {"t1": task("t1", "Digest", run)}
    decision = Decision(reply="On it.", tasks=[recipe("Digest")])
    reply = effects(decision, known)

    reply.fold_repeats()

    [control] = decision.controls
    assert (control.task_id, control.action, control.message) == ("t1", "retry_review", "")

    # "Retry the review" on the same task's card picks the same control.
    decision = Decision(reply="Sure.")
    reply = effects(decision, known, FakeStore(latest="t1"))
    assert CONTINUATION.fullmatch("Retry the review.")
    reply.continue_latest(None)
    assert [(c.task_id, c.action) for c in decision.controls] == [("t1", "retry_review")]


@pytest.mark.parametrize(
    "fields",
    [
        {"schedule": {"kind": "daily"}},
        {"schedule": {"at": "2030-01-01T09:00:00Z"}},
        {"after": ["x"]},
    ],
)
def test_scheduled_or_waiting_task_stays_new(fields: dict[str, Any]):
    known = {"t1": task("t1", "Digest", {"id": "r1", "state": "blocked"})}
    decision = Decision(reply="Scheduled.", tasks=[recipe("Digest", **fields)])
    reply = effects(decision, known)

    reply.fold_repeats()

    assert [r.title for r in decision.tasks] == ["Digest"]
    assert decision.controls == []
    assert decision.reply == "Scheduled."
    assert reply.fresh_index == {0: 0}
    assert reply.followed == {}


def test_two_controls_for_one_task_are_conflicting_changes():
    known = {"t1": task("t1", "Digest", {"id": "r1", "state": "paused"})}
    decision = Decision(
        reply="Done.",
        controls=[
            TaskControl(task_id="t1", action="pause"),
            TaskControl(task_id="t1", action="cancel"),
        ],
    )

    with pytest.raises(ValueError, match="conflicting changes"):
        effects(decision, known).fold_repeats()


def test_try_again_on_a_held_task_picks_its_retryable_prerequisite():
    held = task(
        "t2",
        "Publish",
        None,
        dependency={
            "state": "held",
            "prerequisites": [
                {"task_id": "t3", "retryable": False},
                {"task_id": "t1", "retryable": True},
            ],
        },
    )
    known = {
        "t1": task("t1", "Build", {"id": "r1", "state": "cancelled"}),
        "t2": held,
        "t3": task("t3", "Lint", {"id": "r3", "state": "cancelled"}),
    }
    decision = Decision(reply="I can't.", tasks=[recipe("Something else")])
    reply = effects(decision, known)

    assert CONTINUATION.fullmatch("please try that again!")
    reply.continue_latest({"work": {"task_id": "t2"}})

    assert decision.tasks == []
    assert decision.reply == "I’ll continue in the existing thread."
    [control] = decision.controls
    assert (control.task_id, control.action) == ("t1", "follow_up")
    assert control.message.startswith("Continue the original task.")


def test_new_n_names_created_and_followed_tasks_and_never_a_later_one():
    known = {"t1": task("t1", "Digest", {"id": "r1", "state": "blocked"})}
    decision = Decision(
        reply="Queued.",
        tasks=[
            recipe("Digest"),
            recipe("Build"),
            recipe("Publish", after=["new:0", "new:1"]),
        ],
    )
    store = FakeStore()
    reply = effects(decision, known, store)

    reply.fold_repeats()
    reply.start_tasks(None, {}, {}, [])  # type: ignore[arg-type]

    # Digest became a follow-up of t1; Build and Publish are new, in order.
    assert [r.title for r in store.added] == ["Build", "Publish"]
    assert store.added[1].after == ["t1", "created-0"]
    assert store.messages[0] == "Queued."

    assert after_reference("run-7", 0, [], {}, {}) == "run-7"
    with pytest.raises(ValueError, match="listed before it"):
        after_reference("new:1", 0, [], {0: 0, 1: 1}, {})

    decision = Decision(
        reply="Queued.", tasks=[recipe("Build", after=["new:1"]), recipe("Publish")]
    )
    reply = effects(decision, {}, FakeStore())
    reply.fold_repeats()
    with pytest.raises(ValueError, match="listed before it"):
        reply.start_tasks(None, {}, {}, [])  # type: ignore[arg-type]


class RecordingStore(FakeStore):
    """FakeStore plus the store calls apply_controls, start_tasks and post_reply make."""

    def __init__(self, tasks: dict[str, dict[str, Any]] | None = None, starts: bool = False):
        super().__init__()
        self.tasks = tasks or {}
        self.starts = starts
        self.calls: list[tuple[Any, ...]] = []
        self.extras: list[dict[str, Any]] = []
        self.settings: dict[str, Any] = {"conversation": {"turn_state": "replying"}}

    def get(self, db: Any, table: str, key: str) -> dict[str, Any]:
        return self.tasks[key]

    def task_view(self, db: Any, stored: dict[str, Any]) -> dict[str, Any]:
        return stored

    def mutate_run(self, run_id: str, payload: dict[str, Any], action: str, db: Any) -> None:
        self.calls.append(("mutate_run", run_id, action, payload["request_id"]))

    def starts_now(self, recipe: Recipe) -> bool:
        return self.starts

    def add_run(self, db: Any, task: dict[str, Any], key: str, config: Any, **extra: Any):
        self.calls.append(("add_run", task["id"], key, extra))
        return {"id": f"run-{task['id']}"}

    def put(self, db: Any, table: str, row: dict[str, Any]) -> None:
        self.calls.append(("put", table, row["id"]))

    def message(self, db: Any, body: str, **extra: Any) -> None:
        self.messages.append(body)
        self.extras.append(extra)

    def work(self, run: dict[str, Any]) -> dict[str, Any]:
        return {"run_id": run["id"]}

    def setting(self, db: Any, key: str) -> Any:
        return dict(self.settings[key])

    def set_setting(self, db: Any, key: str, value: Any) -> None:
        self.calls.append(("set_setting", key))
        self.settings[key] = value


class FakeHome:
    """The RemHome methods ReplyEffects calls, recorded with what they were given."""

    def __init__(self, store: RecordingStore) -> None:
        self.store = store
        self.calls: list[tuple[Any, ...]] = []
        self.resumed_by: str | None = None
        self.self_work = SimpleNamespace(
            post_after_reply=lambda db: self.calls.append(("post_after_reply",))
        )

    def pause_run(self, run_id: str, payload: dict[str, Any], db: Any) -> None:
        self.calls.append(("pause_run", run_id))

    def resume_run(
        self, run_id: str, payload: dict[str, Any], *, by: str = "user", db: Any
    ) -> dict[str, Any]:
        self.calls.append(("resume_run", run_id))
        self.resumed_by = by
        return {"run": {"id": run_id, "state": "queued"}}

    def retry_review(self, run_id: str, payload: dict[str, Any], db: Any) -> dict[str, Any]:
        self.calls.append(("retry_review", run_id))
        return {"run": {"id": run_id, "state": "verifying"}}

    def instruct(self, thread_id: str, payload: dict[str, Any], **options: Any) -> dict[str, Any]:
        self.calls.append(("instruct", thread_id, payload["body"], options["notify"]))
        return {"run": {"id": f"run-of-{thread_id}", "state": "queued"}}

    def edit_task(self, task_id: str, payload: dict[str, Any], action: str, **options: Any) -> None:
        self.calls.append(("edit_task", task_id, action, payload))

    def _deliver_files_to(self, db: Any, run_id: str, pending: Any) -> dict[str, Any]:
        self.calls.append(("_deliver_files_to", run_id, pending))
        return {"id": run_id, "state": "queued"}


def runs_db() -> sqlite3.Connection:
    """The one table link_tickets reads."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, body TEXT)")
    return db


def recording(
    decision: Decision,
    tasks: dict[str, dict[str, Any]],
    starts: bool = False,
    dropped: bool = False,
) -> tuple[ReplyEffects, FakeHome]:
    home = FakeHome(RecordingStore(tasks, starts))
    reply = ReplyEffects(home, decision, tasks, "m1", {}, 0.0, dropped)  # type: ignore[arg-type]
    return reply, home


@pytest.mark.parametrize(
    "tasks", [{}, {"t1": task("t1", "Digest", None, deleted_at="2026-11-26T09:00:00Z")}]
)
def test_a_control_on_a_missing_or_deleted_task_is_left_out_and_named(tasks: dict[str, Any]):
    decision = Decision(reply="Paused.", controls=[TaskControl(task_id="t1", action="pause")])
    reply, home = recording(decision, tasks)

    reply.apply_controls(runs_db())

    assert home.calls == []
    title = "“Digest”" if tasks else "“a task”"
    assert decision.reply == (
        "Paused.\n\nI couldn't apply one task change, so I left it out: the pause of "
        f"{title}: it was deleted while I was answering. Everything else in this reply "
        "went ahead."
    )


@pytest.mark.parametrize(
    "control",
    [
        TaskControl(task_id="t1", action="follow_up", message="Keep going"),
        TaskControl(task_id="t1", action="retry_review"),
    ],
)
def test_follow_up_or_retry_on_a_task_that_never_ran_has_no_thread(control: TaskControl):
    tasks = {"t1": task("t1", "Digest", None, active_run=None)}
    reply, home = recording(Decision(reply="On it.", controls=[control]), tasks)

    reply.apply_controls(runs_db())

    assert home.calls == []
    assert "“Digest”: This task has no thread to follow up on." in reply.decision.reply


def test_one_failing_control_is_rolled_back_while_the_others_apply():
    tasks = {
        "a": task("a", "First", {"id": "ra", "state": "blocked", "thread_id": "th-a"}),
        "b": task("b", "Second", {"id": "rb", "state": "blocked", "thread_id": "th-b"}),
        "c": task("c", "Third", {"id": "rc", "state": "blocked", "thread_id": "th-c"}),
    }
    decision = Decision(
        reply="Resuming all three.",
        controls=[TaskControl(task_id=t, action="follow_up", message="Finish it") for t in "abc"],
    )
    reply, home = recording(decision, tasks)
    db = runs_db()
    original = home.instruct

    def instruct(thread_id: str, payload: dict[str, Any], **options: Any) -> dict[str, Any]:
        # Each follow-up writes in the reply's transaction before it can fail.
        db.execute("INSERT INTO runs VALUES(?, ?)", (thread_id, "{}"))
        if thread_id == "th-b":
            raise ValueError("This task already has an active or uncertain run")
        return original(thread_id, payload, **options)

    home.instruct = instruct  # type: ignore[method-assign]

    reply.apply_controls(db)

    # The second follow-up's write was rolled back; the first and third kept theirs.
    assert [r[0] for r in db.execute("SELECT id FROM runs ORDER BY id")] == ["th-a", "th-c"]
    assert [c[1] for c in home.calls if c[0] == "instruct"] == ["th-a", "th-c"]
    assert reply.followed_run == {"id": "run-of-th-c", "state": "queued"}
    assert decision.reply.endswith(
        "I couldn't apply one task change, so I left it out: the follow-up of “Second”: "
        "This task already has an active or uncertain run. Everything else in this reply "
        "went ahead."
    )


def test_pause_resume_cancel_and_update_reach_the_matching_home_calls():
    tasks = {
        "pause": task("pause", "A", None, active_run={"id": "ra", "state": "executing"}),
        "resume": task("resume", "B", {"id": "rb", "state": "paused"}),
        "cancel": task("cancel", "C", None, active_run={"id": "rc", "state": "executing"}),
        "update": task("update", "D", None, revision=7),
    }
    decision = Decision(
        reply="Done.",
        controls=[
            TaskControl(task_id="pause", action="pause"),
            TaskControl(task_id="resume", action="resume"),
            TaskControl(task_id="cancel", action="cancel"),
            TaskControl(task_id="update", action="update", patch={"title": "D2"}),
        ],
    )
    reply, home = recording(decision, tasks)

    reply.apply_controls(runs_db())

    assert home.calls[:2] == [("pause_run", "ra"), ("resume_run", "rb")]
    # The user's own message resumes it as theirs.
    assert home.resumed_by == "user"
    assert reply.followed_run == {"id": "rb", "state": "queued"}
    [(name, run_id, action, request_id)] = home.store.calls
    assert (name, run_id, action) == ("mutate_run", "rc", "cancel")
    assert request_id.startswith("rem-control-")
    [(name, task_id, edit, payload)] = home.calls[2:]
    assert (name, task_id, edit) == ("edit_task", "update", "edit")
    assert payload["expected_revision"] == 7
    assert payload["title"] == "D2"


def test_follow_up_and_retry_review_follow_the_run_they_start():
    tasks = {
        "f": task("f", "A", {"id": "rf", "state": "blocked", "thread_id": "th-f"}, send_files=True),
        "r": task("r", "B", {"id": "rr", "state": "blocked", "thread_id": "th-r"}),
    }
    decision = Decision(
        reply="On it.",
        controls=[
            TaskControl(task_id="f", action="follow_up", message="Again"),
            TaskControl(task_id="r", action="retry_review"),
        ],
    )
    reply, home = recording(decision, tasks)

    reply.apply_controls(runs_db())

    # The reply carries the last followed run, so only the earlier follow-up notifies.
    assert home.calls == [
        ("instruct", "th-f", "Again", True),
        ("_deliver_files_to", "run-of-th-f", "m1"),
        ("retry_review", "rr"),
    ]
    assert reply.followed_run == {"id": "rr", "state": "verifying"}


def test_continue_on_a_running_task_follows_its_run():
    run = {"id": "r1", "state": "executing"}
    known = {"t1": task("t1", "Digest", None, active_run=run)}
    decision = Decision(reply="Starting over.", tasks=[recipe("Digest")])
    reply = effects(decision, known, FakeStore(latest="t1"))

    reply.continue_latest(None)

    assert reply.followed_run is run
    assert decision.reply == "It’s still running in the existing thread."
    assert decision.tasks == [] and decision.controls == []


def test_continue_on_a_paused_task_resumes_it():
    known = {"t1": task("t1", "Digest", {"id": "r1", "state": "paused"})}
    decision = Decision(reply="Hm.")
    reply = effects(decision, known, FakeStore(latest="t1"))

    reply.continue_latest(None)

    assert reply.followed_run is None
    assert decision.reply == "I’ll resume it in the existing thread."
    assert [(c.task_id, c.action) for c in decision.controls] == [("t1", "resume")]


def test_a_task_that_starts_now_with_send_files_gets_the_files_on_its_queued_run():
    decision = Decision(reply="Starting.", tasks=[recipe("Digest", send_files=True)])
    reply, home = recording(decision, {}, starts=True)

    reply.start_tasks(runs_db(), {}, {"note": 1}, [])

    assert home.calls == [("_deliver_files_to", "run-created-0", "m1")]
    [(name, task_id, key, extra), put] = home.store.calls
    assert (name, task_id, key) == ("add_run", "created-0", "message:m1:0")
    assert extra == {"acknowledgement": "Starting.", "message_extra": {"note": 1}}
    assert put == ("put", "tasks", "created-0")
    # The run carries the reply, so start_tasks writes no message of its own.
    assert home.store.messages == []


def test_reply_without_tasks_carries_the_followed_run_and_dropped_actions():
    decision = Decision(reply="It’s still running.", details="More here")
    reply, home = recording(decision, {}, dropped=True)
    reply.followed_run = {"id": "r1", "state": "executing"}

    reply.post_reply(runs_db(), {}, {"note": 1}, {}, ["memory"], [])

    assert home.store.messages == ["It’s still running."]
    [extra] = home.store.extras
    assert extra["identity"] == "reply-m1"
    assert extra["work"] == {"run_id": "r1"}
    assert extra["workflow_actions_dropped"] is True
    assert extra["details"] == "More here"
    assert extra["note"] == 1
    assert home.store.settings["memories"] == ["memory"]
    assert home.calls == []


@pytest.mark.parametrize(
    ("file_extra", "text"),
    [
        ({"files": [{"path": "a.png"}]}, "Here’s what you asked for."),
        ({"files": [{"path": "a.png"}, {"path": "b.png"}]}, "Here are the files."),
        ({"phone_actions": [{"kind": "open"}]}, "Ready on your phone."),
    ],
)
def test_reply_with_tasks_posts_files_in_their_own_message(file_extra: dict[str, Any], text: str):
    decision = Decision(
        reply="Starting.",
        tasks=[recipe("Digest")],
        self_work={"action": "status"},
    )
    reply, home = recording(decision, {})

    reply.post_reply(runs_db(), {}, {}, file_extra, [], [])

    assert home.store.messages == [text]
    assert home.store.extras == [{"identity": "files-m1", **file_extra}]
    assert home.calls == [("post_after_reply",)]
    conversation = home.store.settings["conversation"]
    assert conversation["turn_state"] == "idle"
    assert conversation["processed"] == "m1"
    assert conversation["turn_label"] is None and conversation["draft_reply"] is None


def test_reply_with_tasks_and_no_files_writes_no_message():
    reply, home = recording(Decision(reply="Starting.", tasks=[recipe("Digest")]), {})

    reply.post_reply(runs_db(), {}, {}, {}, [], [])

    assert home.store.messages == []
    assert home.calls == []
    assert home.store.settings["conversation"]["turn_state"] == "idle"
