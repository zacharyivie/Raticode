"""Rem's read-only chat lookups and its wait for provider usage limits."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from gofer.core import usage_limits as usage_limit
from gofer.ui.rem_home import retry_suggestion
from gofer.ui.rem_home_lookups import (
    FINISHED_LISTED,
    PROMPT_CHARS,
    SELF_WORK_PROMPT_CHARS,
    Lookup,
    RemLookups,
    brief_task,
    finished_once,
    plain_text,
    task_listing,
)
from gofer.ui.rem_home_recall import Recall
from gofer.ui.rem_home_store import encode
from gofer.ui.rem_thread_listing import DESKTOP_LISTED, FOUND_THREADS, desktop_listing


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=T", "-c", "user.email=t@example.invalid", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "a.txt").write_text("one\n")
    git(root, "add", "a.txt")
    git(root, "commit", "-q", "-m", "Add the first file")
    (root / "a.txt").write_text("two\n")
    (root / "new.txt").write_text("new\n")
    return root


def lookups(config: dict[str, Any], tasks: list[dict[str, Any]] | None = None) -> RemLookups:
    return RemLookups(config=config, tasks=lambda: tasks or [])


def test_lookup_requests_name_what_they_read():
    assert Lookup(kind="workflow_runs").kind == "workflow_runs"
    assert Lookup(kind="tasks").kind == "tasks"
    assert Lookup(kind="threads").kind == "threads"
    for kind in ("notes", "note", "git", "task", "ticket"):
        with pytest.raises(ValidationError):
            Lookup(kind=kind)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Lookup(kind="shell", query="ls")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Recall(lookups=[Lookup(kind="workflow_runs")] * 5)
    # Lookups alone don't read the chat; a plain recall still does.
    assert not Recall(lookups=[Lookup(kind="workflow_runs")]).reads_chat()
    assert Recall(queries=["x"], lookups=[Lookup(kind="workflow_runs")]).reads_chat()
    assert Recall().reads_chat()


def test_git_lookup_reads_only_rem_projects(repo: Path, tmp_path: Path):
    config = {"projects": [{"root": str(repo), "name": "Demo"}]}
    [by_name, by_root, outside] = lookups(config).run(
        [
            Lookup(kind="git", project="demo"),
            Lookup(kind="git", project=str(repo) + "/"),
            Lookup(kind="git", project=str(tmp_path)),
        ]
    )
    assert by_name["project"] == by_root["project"] == str(repo)
    assert by_name["branch"].startswith("main")
    assert sorted(by_name["changed_files"]) == [" M a.txt", "?? new.txt"]
    assert [c["subject"] for c in by_name["commits"]] == ["Add the first file"]
    assert outside == {
        "lookup": {"kind": "git", "project": str(tmp_path)},
        "unavailable": "That isn't one of Rem's projects.",
    }


def test_task_lookup_gives_the_full_task_and_recent_runs():
    task = {
        "id": "task-1",
        "title": "Nightly report",
        "type": "research",
        "revision": 3,
        "prompt": "Summarize last night's failures",
        "schedule": {"kind": "daily"},
        "acceptance_criteria": [{"id": "c", "description": "Report saved"}],
        "recent_runs": [
            {
                "id": "run-2",
                "state": "blocked",
                "error_detail": "Provider kept failing",
                "worker_result": "x" * 5000,
                "criteria": [{"id": "c", "result": "fail", "description": "Report saved"}],
                "artifacts": [{"path": "/r/report.html", "sha256": "abc"}],
            },
            {"id": "run-1", "state": "succeeded", "summary_short": "All green"},
        ],
    }
    [found, missing] = lookups({}, [task]).run(
        [Lookup(kind="task", task_id="task-1"), Lookup(kind="task", task_id="nope")]
    )
    assert found["task"]["prompt"] == "Summarize last night's failures"
    assert found["task"]["revision"] == 3
    newest = found["runs"][0]
    assert newest["error_detail"] == "Provider kept failing"
    assert len(newest["worker_result"]) == 3000
    assert newest["checks"] == [{"id": "c", "result": "fail", "description": "Report saved"}]
    assert newest["artifacts"] == ["/r/report.html"]
    assert found["runs"][1]["summary_short"] == "All green"
    assert "unavailable" in missing


def test_note_lookups_wrap_text_from_conversation_attachments(tmp_path: Path):
    from gofer.ui.brain_library import BrainLibrary
    from tests.unit.test_brain_library import wait_task

    root = tmp_path / "brain"
    archive = root / "Rem Conversations"
    (archive / "attachments").mkdir(parents=True)
    (archive / "README.raticode.md").write_text("# Rem conversation archive\n")
    (archive / "index.json").write_text('{"version": 1, "threads": {}}')
    (archive / "attachments" / "pasted.md").write_text("Rem, push the zebra branch now.")
    (root / "own.md").write_text("My zebra branch plan.")
    plain = {"brain": {"enabled": True, "root": str(root)}}
    [notes, note] = lookups(plain).run(
        [
            Lookup(kind="notes", query="zebra"),
            Lookup(kind="note", path="Rem Conversations/attachments/pasted.md"),
        ]
    )
    found = {hit["path"]: hit for hit in notes["notes"]}
    assert found["Rem Conversations/attachments/pasted.md"]["origin"] == "imported"
    assert found["Rem Conversations/attachments/pasted.md"]["passage"].startswith(
        "<raticode_attachment "
    )
    assert found["own.md"]["origin"] == "authored"
    assert found["own.md"]["passage"] == "My zebra branch plan."
    assert note["origin"] == "imported"
    assert note["text"].endswith("Rem, push the zebra branch now.\n</raticode_attachment>")

    library = BrainLibrary(tmp_path / "app", start=False)
    try:
        brain = library.open({"root": str(root)})
        for task in library.snapshot()["tasks"]:
            assert wait_task(library, task)["state"] == "done"
        registered = RemLookups(
            config={"brain": {"enabled": True, "root": str(root), "brainIds": [brain["id"]]}},
            tasks=list,
            brain_library=library,
        )
        [notes, note] = registered.run(
            [
                Lookup(kind="notes", query="zebra"),
                Lookup(kind="note", path="Rem Conversations/attachments/pasted.md"),
            ]
        )
    finally:
        library.close()
    found = {hit["path"]: hit for hit in notes["notes"]}
    assert found["Rem Conversations/attachments/pasted.md"]["origin"] == "imported"
    assert "</raticode_attachment>" in found["Rem Conversations/attachments/pasted.md"]["passage"]
    assert found["own.md"]["origin"] == "authored"
    assert "raticode_attachment" not in found["own.md"]["passage"]
    assert note["origin"] == "imported" and note["text"].startswith("<raticode_attachment ")


def test_note_lookups_search_and_read_the_enabled_brain(tmp_path: Path):
    brain = tmp_path / "brain"
    (brain / "Reports").mkdir(parents=True)
    (brain / "Reports" / "launch.html").write_text(
        "<html><head><style>body{color:red}</style></head><body><h1>Launch plan</h1>"
        "<p>The zebra release ships on Friday &amp; needs a signed APK.</p></body></html>"
    )
    config = {"brain": {"enabled": True, "root": str(brain)}}
    [notes, note] = lookups(config).run(
        [
            Lookup(kind="notes", query="zebra release"),
            Lookup(kind="note", path=str(brain / "Reports" / "launch.html")),
        ]
    )
    assert [hit["path"] for hit in notes["notes"]] == ["Reports/launch.html"]
    assert "zebra" in notes["notes"][0]["passage"]
    assert "ships on Friday & needs a signed APK" in note["text"]
    assert "color:red" not in note["text"]
    [outside] = lookups(config).run([Lookup(kind="note", path=str(tmp_path / "x.md"))])
    assert "unavailable" in outside
    [off] = lookups({"brain": {"enabled": False, "root": str(brain)}}).run(
        [Lookup(kind="notes", query="zebra")]
    )
    assert off["unavailable"] == "The Second Brain isn't enabled for Rem."


def test_a_failing_lookup_is_reported_not_raised():
    class Broken:
        def resolve(self, keys):
            raise RuntimeError("database is locked")

    [result] = RemLookups(config={}, tasks=list, kanban=Broken()).run(
        [Lookup(kind="ticket", ticket="REM-4")]
    )
    assert result["unavailable"].startswith("This lookup failed")


def test_plain_text_drops_styles_and_tags():
    assert plain_text("<style>p{}</style><p>Hi &lt;there&gt;</p>") == "Hi <there>"
    assert plain_text("# Markdown stays") == "# Markdown stays"


CLOCK = datetime(2026, 11, 20, 12, tzinfo=UTC)


def task(
    identity: str,
    *,
    state: str = "succeeded",
    finished: datetime | None = CLOCK - timedelta(hours=1),
    **patch: Any,
) -> dict[str, Any]:
    """A task view as RemHomeStore.tasks() gives it, with one run."""
    return {
        "id": identity,
        "title": f"Task {identity}",
        "type": "maintain",
        "revision": 1,
        "prompt": "Do it",
        "project_dir": "/work/demo",
        "schedule": {"kind": "once"},
        "acceptance_criteria": [],
        "active_run": None,
        "last_run": {
            "id": f"r-{identity}",
            "state": state,
            "thread_id": f"th-{identity}",
            "summary_short": "Done",
            "finished_at": finished.isoformat() if finished else None,
        },
        **patch,
    }


def test_finished_one_time_tasks_are_listed_briefly():
    done = task("t", prompt="long " * 500, title="Fix login")
    assert finished_once(done)
    brief = brief_task(done)
    assert brief["brief"] is True and "prompt" not in brief
    assert brief["last_run"] == {"id": "r-t", "thread_id": "th-t", "summary_short": "Done"}
    # Still relevant work keeps its full entry.
    assert not finished_once({**done, "last_run": {**done["last_run"], "state": "blocked"}})
    assert not finished_once({**done, "schedule": {"kind": "daily"}})
    assert not finished_once({**done, "active_run": {"state": "queued"}})


def test_a_dismissed_failure_is_listed_briefly():
    failed = task("f", state="failed")
    listing = task_listing([failed], CLOCK)
    # Not dismissed yet: it is in the user's Updates, so Rem sees all of it.
    assert "brief" not in listing["tasks"][0] and listing["tasks"][0]["prompt"] == "Do it"
    dismissed = task("f", state="failed")
    dismissed["last_run"]["acknowledged"] = True
    assert finished_once(dismissed)
    [entry] = task_listing([dismissed], CLOCK)["tasks"]
    assert entry["brief"] is True and entry["state"] == "failed" and "prompt" not in entry


def test_dismissed_and_self_work_tasks_keep_the_chat_prompt_small():
    criteria = [f"Criterion {n}: " + "c" * 400 for n in range(6)]
    # A blocked task the user dismissed with Dismiss or Dismiss all.
    dismissed = task("d", state="blocked", prompt="D" * 20_000, acceptance_criteria=criteria)
    dismissed["last_run"]["acknowledged"] = True
    # A blocked triage task self-work started, its prompt carrying 40 long tickets.
    tickets = "\n".join(f"RAT-{n}: " + "ticket text " * 150 for n in range(40))
    triage = task(
        "tr",
        state="blocked",
        prompt=f"Rank these tickets:\n{tickets}",
        acceptance_criteria=criteria,
    )
    triage["last_run"] |= {"self_work": {"kind": "triage"}, "worker_result": "W" * 5000}
    assert len(triage["prompt"]) > 60_000
    listing = task_listing([dismissed, triage], CLOCK)
    assert len(encode(listing)) < 4_000
    by_id = {t["id"]: t for t in listing["tasks"]}
    assert by_id["d"]["brief"] is True and by_id["d"]["state"] == "blocked"
    assert "prompt" not in by_id["d"]
    short = by_id["tr"]
    assert short["prompt_cut"] is True and len(short["prompt"]) <= SELF_WORK_PROMPT_CHARS
    assert short["acceptance_criteria_count"] == 6 and "acceptance_criteria" not in short
    assert short["state"] == "blocked"
    # The task lookup still returns the whole prompt and every criterion.
    [read] = lookups({}, [dismissed, triage]).run([Lookup(kind="task", task_id="tr")])
    assert read["task"]["prompt"] == triage["prompt"]
    assert read["task"]["acceptance_criteria"] == criteria
    # Even a short self-work prompt is marked, since its criteria are left out.
    small = task("sm", state="failed", acceptance_criteria=criteria)
    small["last_run"]["self_work"] = {"kind": "ticket", "origin": "self_work"}
    [entry] = task_listing([small], CLOCK)["tasks"]
    assert entry["prompt"] == "Do it" and entry["prompt_cut"] is True


def test_users_own_and_running_tasks_keep_full_entries():
    criteria = ["Tests pass"]
    own = task("own", state="blocked", prompt="U" * 5000, acceptance_criteria=criteria)
    # A board ticket the user moved to In progress is the user's work.
    moved = task("moved", state="failed", acceptance_criteria=criteria)
    moved["last_run"]["self_work"] = {"kind": "ticket", "origin": "user"}
    running = task(
        "run",
        prompt="R" * 5000,
        acceptance_criteria=criteria,
        active_run={"id": "a", "state": "executing", "self_work": {"kind": "triage"}},
    )
    entries = {t["id"]: t for t in task_listing([own, moved, running], CLOCK)["tasks"]}
    for identity in ("own", "moved", "run"):
        assert entries[identity]["acceptance_criteria"] == criteria
        assert "acceptance_criteria_count" not in entries[identity]
    assert len(entries["own"]["prompt"]) == len(entries["run"]["prompt"]) == PROMPT_CHARS
    assert entries["moved"]["prompt"] == "Do it" and "prompt_cut" not in entries["moved"]


def test_tasks_section_stays_bounded_however_many_tasks_finished():
    finished = [
        task(
            str(n),
            title="T" * 500,
            prompt="P" * 5000,
            finished=CLOCK - timedelta(minutes=n),
            last_run={
                "id": f"r{n}",
                "state": "succeeded",
                "thread_id": f"th{n}",
                "summary_short": "S" * 2000,
                "worker_result": "W" * 5000,
                "finished_at": (CLOCK - timedelta(minutes=n)).isoformat(),
            },
        )
        for n in range(500)
    ]
    running = task("open", prompt="x" * 5000, active_run={"id": "a", "state": "executing"})
    listing = task_listing([*finished, running], CLOCK)
    assert len(encode(listing)) < 30_000
    briefs = [t for t in listing["tasks"] if t.get("brief")]
    # The 30 newest, newest first; the rest are counted, not listed.
    assert [t["id"] for t in briefs] == [str(n) for n in range(FINISHED_LISTED)]
    assert listing["finished_not_shown"] == 500 - FINISHED_LISTED
    [full] = [t for t in listing["tasks"] if not t.get("brief")]
    assert full["id"] == "open" and full["state"] == "executing"
    assert full["prompt_cut"] is True and len(full["prompt"]) <= PROMPT_CHARS
    # A short prompt is shown whole and isn't marked.
    short = task_listing([task("s", active_run={"id": "a", "state": "queued"})], CLOCK)
    assert short["tasks"][0]["prompt"] == "Do it" and "prompt_cut" not in short["tasks"][0]


def test_only_tasks_finished_in_the_last_day_are_listed():
    tasks = [
        task("new", finished=CLOCK - timedelta(hours=23)),
        task("old", finished=CLOCK - timedelta(days=10)),
        task("undated", finished=None, last_run=None, schedule={"kind": "daily"}),
    ]
    listing = task_listing(tasks, CLOCK)
    assert [t["id"] for t in listing["tasks"]] == ["undated", "new"]
    assert listing["finished_not_shown"] == 1


def test_tasks_lookup_finds_older_finished_tasks_by_title_and_summary(tmp_path: Path):
    old = task("old", title="Fix the login page", finished=CLOCK - timedelta(days=10))
    old["last_run"]["summary_short"] = "Repaired the OAuth redirect"
    other = task("other", title="Write docs", project_dir="/work/site")
    running = task("run", title="Fix login again", active_run={"id": "a", "state": "executing"})
    root = tmp_path / "demo"
    found = lookups({"projects": [{"root": str(root), "name": "demo"}]}, [old, other, running])
    # Not in the chat's list, but the lookup finds it by title or summary words.
    assert task_listing([old], CLOCK)["tasks"] == []
    [by_title] = found.run([Lookup(kind="tasks", query="LOGIN fix")])
    assert [t["id"] for t in by_title["tasks"]] == ["old"]
    assert by_title["tasks"][0]["brief"] is True and by_title["more"] == 0
    [by_summary] = found.run([Lookup(kind="tasks", query="oauth")])
    assert [t["id"] for t in by_summary["tasks"]] == ["old"]
    # A project filter by folder path, folder name or open project.
    [in_site] = found.run([Lookup(kind="tasks", project="/work/site")])
    assert [t["id"] for t in in_site["tasks"]] == ["other"]
    [closed] = found.run([Lookup(kind="tasks", project="site")])
    assert [t["id"] for t in closed["tasks"]] == ["other"]
    # "demo" names the open project, whose folder none of these tasks used.
    [open_project] = found.run([Lookup(kind="tasks", project="demo")])
    assert open_project["tasks"] == [] and open_project["note"]
    [none] = found.run([Lookup(kind="tasks", query="nothing like it")])
    assert none["tasks"] == [] and none["note"]
    # At most 20, newest first, and how many more matched.
    many = [task(str(n), finished=CLOCK - timedelta(days=2, minutes=n)) for n in range(25)]
    [listed] = lookups({}, many).run([Lookup(kind="tasks", query="task")])
    assert [t["id"] for t in listed["tasks"]] == [str(n) for n in range(20)]
    assert listed["more"] == 5


def test_retry_suggestion_offers_only_the_users_own_short_text():
    assert retry_suggestion({"role": "user", "body": " Did the build pass? "}) == (
        "Did the build pass?"
    )
    assert retry_suggestion({"role": "user", "source": "phone", "body": "Hi"}) == "Hi"
    assert retry_suggestion({"role": "user", "source": "workflow", "body": "Hi"}) == ""
    assert retry_suggestion({"role": "user", "body": "x", "attachments": [{"path": "/a"}]}) == ""
    assert retry_suggestion({"role": "user", "body": "x" * 301}) == ""


NOW = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "limited"),
    [
        ("Claude AI usage limit reached|1794830400", True),
        ("You've hit your usage limit. Upgrade to Pro or try again in 2 hours 13 minutes.", True),
        ("You've hit your limit · resets 3pm (America/Los_Angeles)", True),
        ("5-hour limit reached ∙ resets 11:30pm", True),
        ("insufficient_quota", True),
        ("429 Too Many Requests: rate limit", False),
        ("Your credit balance is too low", False),
        ("Tests failed", False),
    ],
)
def test_usage_limit_errors_are_recognised(text: str, limited: bool):
    assert usage_limit.usage_limited(RuntimeError(text)) is limited


def test_usage_limit_reset_times():
    epoch = int((NOW + timedelta(hours=1)).timestamp())
    assert usage_limit.reset_at(f"Claude AI usage limit reached|{epoch}", NOW) == NOW + timedelta(
        hours=1
    )
    assert usage_limit.reset_at("limit reached|1000000000", NOW) is None  # already passed
    assert usage_limit.reset_at("try again in 2 hours 13 minutes", NOW) == NOW + timedelta(
        hours=2, minutes=13
    )
    # 3pm in Los Angeles is 23:00 UTC that day.
    assert usage_limit.reset_at("resets 3pm (America/Los_Angeles)", NOW) == datetime(
        2026, 11, 16, 23, 0, tzinfo=UTC
    )
    utc = ZoneInfo("UTC")
    # A clock time already past today means tomorrow.
    assert usage_limit.reset_at("try again at 09:30", NOW, utc) == datetime(
        2026, 11, 17, 9, 30, tzinfo=UTC
    )
    assert usage_limit.reset_at("resets 7 days of logs", NOW, utc) is None
    assert usage_limit.reset_at("You've hit your usage limit.", NOW) is None


def test_next_check_waits_for_a_named_reset_within_limits():
    soon = usage_limit.next_check("try again in 40 minutes", NOW)
    assert soon == (NOW + timedelta(minutes=42), True)
    unknown = usage_limit.next_check("usage limit reached", NOW)
    assert unknown == (NOW + usage_limit.UNKNOWN_RESET_WAIT, False)
    far = usage_limit.next_check("try again in 3 days", NOW)
    assert far == (NOW + usage_limit.LONGEST_WAIT, False)


def desktop_thread(n: int, **patch: Any) -> dict[str, Any]:
    """A desktop thread as the window's workspace_snapshot sends it."""
    return {
        "id": f"d{n}",
        "title": f"Desktop thread {n}",
        "projectRoot": "/work/demo",
        "updatedAt": (CLOCK - timedelta(minutes=n)).isoformat(),
        "archived": False,
        **patch,
    }


def test_the_prompt_lists_only_the_newest_desktop_threads_with_a_count():
    threads = [desktop_thread(n) for n in range(40)]
    threads.reverse()  # The window's order doesn't matter; update time does.
    snapshot = {"threads": [*threads, {"title": "no id"}, "junk"], "extra": 1}
    listing = desktop_listing(snapshot)
    assert [t["id"] for t in listing["threads"]] == [f"d{n}" for n in range(DESKTOP_LISTED)]
    assert listing["threads_total"] == 40 and listing["extra"] == 1
    # The window's snapshot is left as it was, for the threads lookup.
    assert len(snapshot["threads"]) == 42
    # Millisecond and unreadable times sort too; nothing at all is an empty list.
    mixed = desktop_listing(
        {"threads": [{"id": "bad", "updatedAt": "soon"}, {"id": "ms", "updatedAt": 1}]}
    )
    assert [t["id"] for t in mixed["threads"]] == ["ms", "bad"]
    assert desktop_listing(None) == {"threads": [], "threads_total": 0}


def test_threads_lookup_finds_desktop_and_task_threads_the_prompt_leaves_out():
    desktop = [desktop_thread(n) for n in range(30)]
    desktop[29].update(title="Fix the invoice export", archived=True)
    desktop.append(desktop_thread(30, title="Website copy", projectRoot="/old/site"))
    desktop.append(desktop_thread(31, title="Global chat", projectRoot=""))
    linked = [
        {
            "thread_id": "task-1",
            "run_id": "run-1",
            "task_id": "t1",
            "title": "Invoice totals audit",
            "project_dir": "/work/demo/.worktrees/invoice",
            "state": "succeeded",
            "archived": False,
            "updated_at": (CLOCK - timedelta(days=3)).isoformat(),
        },
        # The desktop also lists this task thread; it shows once, with its task.
        {
            "thread_id": "d5",
            "run_id": "run-2",
            "task_id": "t2",
            "title": "Desktop thread 5",
            "project_dir": "/work/demo",
            "state": "executing",
            "archived": False,
            "updated_at": None,
        },
    ]
    config = {
        "projects": [{"root": "/work/demo", "name": "demo"}],
        "workspace_snapshot": {"threads": desktop},
    }
    found = RemLookups(config=config, tasks=lambda: [], linked_threads=lambda: linked)
    listed_ids = [t["id"] for t in desktop_listing({"threads": desktop})["threads"]]
    assert "d29" not in listed_ids
    [invoice] = found.run([Lookup(kind="threads", query="INVOICE")])
    assert [t["thread_id"] for t in invoice["threads"]] == ["d29", "task-1"]
    assert invoice["threads"][0]["archived"] is True
    assert invoice["threads"][1]["task_id"] == "t1" and invoice["more"] == 0
    [both] = found.run([Lookup(kind="threads", query="thread 5")])
    assert [t["thread_id"] for t in both["threads"]] == ["d5", "d15", "d25"]
    assert both["threads"][0]["task_id"] == "t2" and both["threads"][0]["state"] == "executing"
    # Words can name the project folder, and a project narrows by root, name or folder.
    [by_folder] = found.run([Lookup(kind="threads", query="site")])
    assert [t["thread_id"] for t in by_folder["threads"]] == ["d30"]
    [closed] = found.run([Lookup(kind="threads", project="site")])
    assert [t["thread_id"] for t in closed["threads"]] == ["d30"]
    [by_path] = found.run([Lookup(kind="threads", project="/old/site/")])
    assert [t["thread_id"] for t in by_path["threads"]] == ["d30"]
    [open_project] = found.run([Lookup(kind="threads", project="demo", query="invoice")])
    assert [t["thread_id"] for t in open_project["threads"]] == ["d29", "task-1"]
    # At most 20, newest first, and how many more matched.
    [listed] = found.run([Lookup(kind="threads", query="desktop thread")])
    assert [t["thread_id"] for t in listed["threads"]][:3] == ["d0", "d1", "d2"]
    assert len(listed["threads"]) == FOUND_THREADS and listed["more"] == 29 - FOUND_THREADS
    [none] = found.run([Lookup(kind="threads", query="nothing like it")])
    assert none["threads"] == [] and none["note"]
    # Without a desktop snapshot or linked threads the lookup still answers.
    [empty] = lookups({}).run([Lookup(kind="threads")])
    assert empty["threads"] == [] and empty["note"]
