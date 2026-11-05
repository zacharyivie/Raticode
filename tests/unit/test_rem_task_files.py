"""A task started by the user's request for a file attaches it to its finished update."""

from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.rem_home import RemHome
from gofer.ui.rem_home_models import Recipe
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_home import recipe

FIND_VIDEO = (
    "Find a short, suitable video file to send the user. Return its verified absolute path "
    "and size so it can be attached in chat; keep it within the 10 MB file limit."
)


def ask(home: RemHome, request_id: str, body: str, decision: dict[str, Any]) -> str:
    """Send a chat message that Rem answers with ``decision``; return Rem's prompt."""
    prompts: list[str] = []

    def generate(prompt: str, config: Any, cancel: Any) -> dict[str, Any]:
        prompts.append(prompt)
        return decision

    home.generate = generate
    home.submit_message({"request_id": request_id, "body": body})
    home._reply()
    return prompts[-1]


def latest_run(home: RemHome, title: str) -> dict[str, Any]:
    task = next(t for t in home.store.tasks() if t["title"] == title)
    run = task.get("active_run") or task["last_run"]
    with home.store.read() as db:
        return home.store.get(db, "runs", run["id"])


def finish(
    home: RemHome,
    run: dict[str, Any],
    worker_result: str,
    update: dict[str, Any] | Exception,
    *,
    state: str = "succeeded",
) -> tuple[dict[str, Any], str]:
    """Finish ``run`` with this worker result; return its chat update and the update prompt."""
    with home.store.connect() as db:
        current = home.store.get(db, "runs", run["id"])
        current["worker_result"] = worker_result
        current["review_verdict"] = {"criteria": [], "feedback": "No repair needed."}
        home.store.put(db, "runs", current)
    prompts: list[str] = []

    def generate(prompt: str, config: Any, cancel: Any) -> dict[str, Any]:
        prompts.append(prompt)
        if isinstance(update, Exception):
            raise update
        return update

    home.generate = generate
    assert home._finish(run["id"], state, "Found it.", epoch=current["epoch"])
    home.deliver_outcomes()
    message = next(
        m
        for m in home.store.conversation()["messages"]
        if m["id"].startswith(f"finished-{run['id']}")
    )
    return message, prompts[-1] if prompts else ""


@pytest.fixture
def outside() -> Iterator[Path]:
    """A private folder outside pytest's base folder.

    Other tests' backends put ``.gofer-trusted-bin`` next to their data folders there,
    which rightly makes that whole base folder Raticode's own data.
    """
    folder = Path(tempfile.mkdtemp(prefix="rem-task-files-"))
    yield folder
    shutil.rmtree(folder)


@pytest.fixture
def video(home: RemHome, tmp_path: Path, outside: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    home.configure(
        {"provider": "codex", "model": "cli-default", "projects": [{"root": str(project)}]}
    )
    # Outside the open project, like the mobile worktree's sample clip.
    clip = outside / "Raticode Mobile" / "assets" / "rem-sample.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"\x00" * 17051)
    return clip


def test_a_found_video_is_attached_to_the_update_and_routed_to_the_request(
    home: RemHome, video: Path
):
    chat_prompt = ask(
        home,
        "video",
        "Send me a video please.",
        {
            "reply": "I'll find a short video to send you.",
            "tasks": [
                recipe(
                    None,
                    title="Find a video to share",
                    type="errand",
                    prompt=FIND_VIDEO,
                    send_files=True,
                )
            ],
        },
    )
    assert "send_files:true" in chat_prompt
    task = next(t for t in home.store.tasks() if t["title"] == "Find a video to share")
    assert task["send_files"] is True
    run = latest_run(home, "Find a video to share")
    assert run["deliver_files_to"] == "video"

    update, prompt = finish(
        home,
        run,
        f"I found a short sample video.\n\n**File:** `{video}`\n\n- **Size:** 17,051 bytes",
        {"summary": "Here's a 3-second sample video.", "files": [str(video)]},
    )
    assert '"request": "Send me a video please."' in prompt
    assert "files:[]" in prompt and "Raticode attaches each file" in prompt
    assert update["kind"] == "task_update" and update["work"]["run_id"] == run["id"]
    assert update["body"] == "Here's a 3-second sample video."
    assert update["files"] == [
        {"name": "rem-sample.mp4", "size": 17051, "path": str(video.resolve())}
    ]
    # The phone that sent "Send me a video please." receives it (reply_to).
    assert update["reply_to"] == "video"


def test_update_files_keep_every_reply_file_check(home: RemHome, video: Path, outside: Path):
    ask(
        home,
        "send",
        "Find the clip and send it to me",
        {"reply": "Looking.", "tasks": [recipe(None, type="errand", send_files=True)]},
    )
    run = latest_run(home, "Check the feature")
    guessed = outside / "guessed.mp4"
    guessed.write_bytes(b"x")
    secret = outside / "other" / "auth.json"
    secret.parent.mkdir()
    secret.write_text("{}")
    big = outside / "big.mp4"
    with big.open("wb") as handle:
        handle.truncate(10 * 1024 * 1024 + 1)
    update, _ = finish(
        home,
        run,
        f"Found `{video}`, also `{secret}`, `{big}` and `{outside / 'gone.mp4'}`.",
        {
            "summary": "Here it is.",
            "files": [
                str(video),
                str(guessed),
                str(secret),
                str(big),
                str(outside / "gone.mp4"),
                "relative.mp4",
            ],
        },
    )
    assert [f["name"] for f in update["files"]] == ["rem-sample.mp4"]
    assert update["reply_to"] == "send"
    body = update["body"]
    # The worker never reported this path, so Rem won't guess at it.
    assert "guessed.mp4: its path isn’t in our conversation" in body
    # Secret-looking files need the user's request to name the exact path.
    assert "auth.json: it may hold passwords" in body
    assert "big.mp4: it’s larger than 10 MB" in body
    assert "gone.mp4: that file doesn’t exist" in body
    assert "relative.mp4: that file doesn’t exist" in body

    # Naming the exact path in the request releases the secret-looking file.
    ask(
        home,
        "named",
        f"Find and send me {secret}",
        {
            "reply": "Looking.",
            "tasks": [recipe(None, title="Named", type="errand", send_files=True)],
        },
    )
    named, _ = finish(
        home,
        latest_run(home, "Named"),
        f"It's at `{secret}`.",
        {"summary": "Here it is.", "files": [str(secret)]},
    )
    assert [f["name"] for f in named["files"]] == ["auth.json"]


def test_tasks_nobody_asked_files_from_never_attach_any(home: RemHome, video: Path):
    # A chat task without send_files.
    ask(home, "fix", "Fix the feature", {"reply": "Starting.", "tasks": [recipe(None)]})
    run = latest_run(home, "Check the feature")
    assert "deliver_files_to" not in run
    update, prompt = finish(
        home, run, f"Done; sample at `{video}`.", {"summary": "Done.", "files": [str(video)]}
    )
    assert "files" not in update and "reply_to" not in update
    assert "files:[]" not in prompt and '"request"' not in prompt

    # A blocked run that was asked for a file attaches nothing.
    ask(
        home,
        "blocked",
        "Send me the clip",
        {
            "reply": "Looking.",
            "tasks": [recipe(None, title="Blocked", type="errand", send_files=True)],
        },
    )
    blocked, _ = finish(
        home,
        latest_run(home, "Blocked"),
        f"Partly done: `{video}`.",
        {"summary": "Stopped.", "files": [str(video)]},
        state="blocked",
    )
    assert "files" not in blocked


def test_only_one_time_schedules_from_a_request_send_files(home: RemHome):
    soon = datetime.now(UTC) - timedelta(minutes=1)
    tasks = {}
    with home.store.connect() as db:
        for title, schedule in (
            ("Daily", {"kind": "daily", "timezone": "UTC", "local_time": "09:00"}),
            ("Later", {"kind": "once", "timezone": "UTC", "at": soon.isoformat()}),
        ):
            task = home.store.add_task(
                db,
                Recipe.model_validate(
                    recipe(None, title=title, type="errand", schedule=schedule, send_files=True)
                ),
                context={"source_message_id": "asked"},
            )
            task["next_occurrence_at"] = soon.isoformat()
            home.store.put(db, "tasks", task)
            tasks[title] = task["id"]
    home.store.due(datetime.now(UTC))
    daily = latest_run(home, "Daily")
    later = latest_run(home, "Later")
    # A repeating schedule never sends files on its own; a one-time request still does.
    assert "deliver_files_to" not in daily
    assert later["deliver_files_to"] == "asked"


def test_a_chat_follow_up_asking_for_the_file_attaches_it(home: RemHome, video: Path):
    ask(home, "first", "Look for clips", {"reply": "Looking.", "tasks": [recipe(None)]})
    first = latest_run(home, "Check the feature")
    finish(home, first, "Found some.", {"summary": "Found some clips."})
    task_id = first["task_id"]

    # Following up without asking for a file attaches nothing.
    ask(
        home,
        "more",
        "Look again",
        {
            "reply": "Looking again.",
            "controls": [{"task_id": task_id, "action": "follow_up", "message": "Look again"}],
        },
    )
    again = latest_run(home, "Check the feature")
    assert again["id"] != first["id"] and "deliver_files_to" not in again
    finish(home, again, "Found more.", {"summary": "Found more."})

    ask(
        home,
        "send",
        "Send me the best one",
        {
            "reply": "Getting it.",
            "controls": [
                {
                    "task_id": task_id,
                    "action": "follow_up",
                    "message": "Send the best clip's path",
                    "patch": {"send_files": True},
                }
            ],
        },
    )
    run = latest_run(home, "Check the feature")
    assert run["deliver_files_to"] == "send"
    update, _ = finish(
        home, run, f"Best: `{video}`.", {"summary": "Here's the best.", "files": [str(video)]}
    )
    assert update["files"][0]["name"] == "rem-sample.mp4" and update["reply_to"] == "send"


def test_an_update_whose_files_were_all_refused_does_not_claim_them(
    home: RemHome, video: Path, outside: Path
):
    ask(
        home,
        "cat",
        "Send me a picture of a cat",
        {"reply": "Looking.", "tasks": [recipe(None, type="errand", send_files=True)]},
    )
    missing = outside / "cat.jpg"
    update, _ = finish(
        home,
        latest_run(home, "Check the feature"),
        f"I downloaded a cat photo and checked that it opens.\n\n**File:** `{missing}`",
        {"summary": "A picture of a cat is attached.", "files": [str(missing)]},
    )
    assert update["body"] == (
        "I downloaded a cat photo and checked that it opens.\n\n"
        "I couldn’t attach cat.jpg: that file doesn’t exist."
    )
    assert "files" not in update


def test_update_failure_is_logged_and_still_reports(
    home: RemHome, video: Path, caplog: pytest.LogCaptureFixture
):
    ask(
        home,
        "send",
        "Send me a video",
        {"reply": "Looking.", "tasks": [recipe(None, type="errand", send_files=True)]},
    )
    with caplog.at_level(logging.WARNING, logger="gofer.ui.rem_home"):
        update, _ = finish(
            home, latest_run(home, "Check the feature"), f"`{video}`", RuntimeError("down")
        )
    assert update["body"] == "Found it." and "files" not in update
    assert "couldn't write the update" in caplog.text


def _clip_in_raticode_data(home: RemHome) -> Path:
    """A video in Raticode's own data, which is never sent unless the user names it."""
    clip = home.data_dir / "rem-work" / "clips" / "dock duplicate you know.MP4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"\x00" * 2048)
    return clip


def test_a_link_request_handed_to_a_worker_gets_a_link_not_the_file(home: RemHome, video: Path):
    clip = _clip_in_raticode_data(home)
    ask(
        home,
        "link",
        "Link me the video",
        {
            "reply": "I'll find it.",
            "tasks": [recipe(None, title="Find the video", type="errand", send_files=True)],
        },
    )
    run = latest_run(home, "Find the video")
    update, prompt = finish(
        home,
        run,
        f"Found the clip at `{clip}`.",
        {"summary": "Here's the video.", "files": [str(clip)]},
    )
    assert "Raticode adds a link to each file" in prompt and "attaches each file" not in prompt
    assert update["body"] == f"Here's the video.\n\n[{clip.name}](<{clip}>)"
    assert "files" not in update and "reply_to" not in update
    assert "may hold passwords" not in update["body"]


def test_followed_up_and_scheduled_link_requests_get_links_too(home: RemHome, video: Path):
    ask(home, "first", "Look for clips", {"reply": "Looking.", "tasks": [recipe(None)]})
    first = latest_run(home, "Check the feature")
    finish(home, first, "Found some.", {"summary": "Found some clips."})
    ask(
        home,
        "link",
        "Give me a link to the best one",
        {
            "reply": "Getting it.",
            "controls": [
                {
                    "task_id": first["task_id"],
                    "action": "follow_up",
                    "message": "Name the best clip's path",
                    "patch": {"send_files": True},
                }
            ],
        },
    )
    update, _ = finish(
        home,
        latest_run(home, "Check the feature"),
        f"Best: `{video}`.",
        {"summary": "Here's the best.", "files": [str(video)]},
    )
    assert update["body"] == f"Here's the best.\n\n[{video.name}](<{video}>)"
    assert "files" not in update

    # A one-time task scheduled from a link request links its file when it runs.
    soon = datetime.now(UTC) - timedelta(minutes=1)
    with home.store.connect() as db:
        home.store.message(db, "Link me the clip tomorrow", identity="later", role="user")
        task = home.store.add_task(
            db,
            Recipe.model_validate(
                recipe(
                    None,
                    title="Later",
                    type="errand",
                    schedule={"kind": "once", "timezone": "UTC", "at": soon.isoformat()},
                    send_files=True,
                )
            ),
            context={"source_message_id": "later"},
        )
        task["next_occurrence_at"] = soon.isoformat()
        home.store.put(db, "tasks", task)
    home.store.due(datetime.now(UTC))
    later = latest_run(home, "Later")
    assert later["deliver_files_to"] == "later"
    update, _ = finish(
        home, later, f"Found `{video}`.", {"summary": "Linked.", "files": [str(video)]}
    )
    assert update["body"] == f"Linked.\n\n[{video.name}](<{video}>)" and "files" not in update


def test_a_link_update_never_links_a_guessed_or_missing_path(
    home: RemHome, video: Path, outside: Path
):
    ask(
        home,
        "link",
        "link me the clip",
        {"reply": "Looking.", "tasks": [recipe(None, type="errand", send_files=True)]},
    )
    guessed = outside / "guessed.mp4"
    guessed.write_bytes(b"x")
    update, _ = finish(
        home,
        latest_run(home, "Check the feature"),
        f"Found `{video}`.",
        {"summary": "Here.", "files": [str(video), str(guessed), str(outside / "gone.mp4")]},
    )
    assert update["body"].startswith(f"Here.\n\n[{video.name}](<{video}>)\n\n")
    assert "guessed.mp4: its path isn’t in our conversation" in update["body"]
    assert "gone.mp4: that file doesn’t exist" in update["body"]
    assert "files" not in update
