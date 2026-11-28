"""A research round starts where the last one of its project and angle stopped.

Each run records the project's commit: the tip of dev, its integration branch, when it has one,
else the checkout's HEAD. The next round's prompt (shown in its task details) names
the last report that project and angle saved, the commit that run started at, and the project's
tickets closed or declined in the last 14 days, so it reads on from there and doesn't propose
declined work again. Fake providers and isolated data only.
"""

import json
import re
import stat
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.core.usage_ledger import record_rem_call
from gofer.ui import rem_research_start as research_start
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_kanban import read_tickets
from gofer.ui.rem_worker_background import CONTEXT_USAGE_FILE, run_folder
from tests.unit.test_rem_kanban import add, tick, wait_for
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_rem_self_work_windows import Clock
from tests.unit.test_self_work_fill import quiet
from tests.unit.test_self_work_research_rounds import ANGLES, ideas, window

FEATURES = ANGLES[0]


def git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(project), "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def commit(project: Path, message: str) -> str:
    git(project, "commit", "-q", "--allow-empty", "--no-verify", "-m", message)
    return git(project, "rev-parse", "HEAD")


def research(home: RemHome, title: str) -> list[dict[str, Any]]:
    """Self-work's research runs with this title, oldest first, each with its task."""
    with home.store.read() as db:
        rows = db.execute(
            "SELECT r.body, t.body FROM runs r JOIN tasks t ON t.id = r.task "
            "WHERE json_extract(r.body, '$.self_work.kind')='research' "
            "AND json_extract(t.body, '$.title')=? ORDER BY json_extract(r.body, '$.created_at')",
            (title,),
        ).fetchall()
    return [{**json.loads(run), "task": json.loads(task)} for run, task in rows]


def save_report(home: RemHome, run_id: str, path: Path) -> None:
    path.write_text("<html><body>Round one findings</body></html>")
    with home.store.connect() as db:
        run = home.store.get(db, "runs", run_id)
        run.setdefault("artifacts", []).append(
            {"path": str(path), "sha256": "x", "epoch": run["epoch"], "superseded": False}
        )
        home.store.put(db, "runs", run)


def test_the_next_round_starts_from_the_last_report_its_commit_and_recent_closes(board):
    home, fake, project = board
    git(project, "init", "-q")
    first = commit(project, "first")
    clock = Clock(home)
    for number, title in enumerate(ANGLES):
        fake.answers[title] = ideas(f"Idea {number} a")
    window(home, clock)
    clock.to(minutes=1)
    tick(home)
    wait_for(lambda: quiet(home), 8)

    (done,) = research(home, FEATURES)
    # Without a dev branch the checkout's HEAD is recorded.
    assert done["self_work"]["commit"] == first
    assert done["self_work"]["branch"] == ""
    assert done["self_work"]["angle"] == 0
    # The first round has nothing to start from yet.
    assert "Where the last round stopped" not in done["task"]["prompt"]
    report = project / "features-round-1.html"
    save_report(home, done["id"], report)

    # Between rounds: new commits, a ticket done and an idea declined.
    second = commit(project, "second")
    shipped = add(home, "Ship dark mode", status="backlog")
    home.kanban.move("user", {"ticket_id": shipped["id"], "expected_revision": 1, "column": "done"})
    with home.store.read() as db:
        idea = next(t for t in read_tickets(db) if t["title"] == "Idea 0 a")
    home.kanban.decide(
        "user", {"ticket_id": idea["id"], "expected_revision": idea["revision"]}, "decline"
    )

    for number, title in enumerate(ANGLES):
        fake.answers[title] = ideas(f"Idea {number} b")
    clock.to(minutes=62)
    tick(home)
    wait_for(lambda: quiet(home), 8)

    later = research(home, FEATURES)[-1]
    assert later["id"] != done["id"]
    assert later["self_work"]["commit"] == second
    prompt = later["task"]["prompt"]
    start = prompt[prompt.index("Where the last round stopped:") :].split("\n\n")[0]
    assert str(report) in start
    assert f"the commits after {first[:12]} (now {second[:12]})" in start
    assert f"REM-{shipped['number']} Ship dark mode (done)" in start
    assert f"REM-{idea['number']} Idea 0 a (declined)" in start
    assert "don't re-propose declined work" in start
    assert len(start.splitlines()) == 3
    # Other angles saved no report, so they get only the recent closes.
    other = research(home, ANGLES[1])[-1]
    assert "Start from the last report" not in other["task"]["prompt"]
    assert "Ship dark mode (done)" in other["task"]["prompt"]


def test_a_project_with_a_landing_branch_records_its_tip_not_the_checkouts(board):
    home, fake, project = board
    git(project, "init", "-q")
    commit(project, "base")
    git(project, "branch", "dev")
    git(project, "checkout", "-q", "-b", "test")
    # The checkout is on another branch, ahead of dev.
    checkout = commit(project, "only on test")

    def land(message: str) -> str:
        git(project, "checkout", "-q", "dev")
        tip = commit(project, message)
        git(project, "checkout", "-q", "test")
        return tip

    first = land("landed one")
    # Without a decided landing branch, it's the checkout's commit; there's no built-in dev.
    assert research_start.project_commit(str(project)) == ("", checkout)
    assert research_start.project_commit(str(project), "dev") == ("dev", first)
    home.change_decisions(
        {"action": "add", "topic": "branch.integration", "answer": "dev", "project": str(project)}
    )
    clock = Clock(home)
    for number, title in enumerate(ANGLES):
        fake.answers[title] = ideas(f"Idea {number} a")
    window(home, clock)
    clock.to(minutes=1)
    tick(home)
    wait_for(lambda: quiet(home), 8)

    (done,) = research(home, FEATURES)
    assert done["self_work"]["commit"] == first != checkout
    assert done["self_work"]["branch"] == "dev"
    save_report(home, done["id"], project / "features-round-1.html")

    second = land("landed two")
    commit(project, "more on test")
    for number, title in enumerate(ANGLES):
        fake.answers[title] = ideas(f"Idea {number} b")
    clock.to(minutes=62)
    tick(home)
    wait_for(lambda: quiet(home), 8)

    later = research(home, FEATURES)[-1]
    assert later["id"] != done["id"]
    assert later["self_work"]["commit"] == second
    assert later["self_work"]["branch"] == "dev"
    assert f"the commits on dev after {first[:12]} (now {second[:12]})" in later["task"]["prompt"]


def test_a_project_without_dev_records_head_and_one_outside_git_records_nothing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    commit(repo, "base")
    git(repo, "checkout", "-q", "-b", "feature")
    head = commit(repo, "on feature")
    assert research_start.project_commit(str(repo)) == ("", head)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert research_start.project_commit(str(plain)) == ("", "")


def ticket(number: int, status: str, days: float, **fields: Any) -> dict[str, Any]:
    at = datetime(2026, 11, 17, tzinfo=UTC) - timedelta(days=days)
    return {
        "number": number,
        "title": f"Ticket {number}",
        "status": status,
        "project_dir": "/p",
        "updated_at": at.isoformat(),
        **fields,
    }


def test_recent_closes_are_newest_first_capped_and_only_this_projects():
    clock = datetime(2026, 11, 17, tzinfo=UTC)
    tickets = [ticket(n, "done", n / 10) for n in range(1, 41)] + [
        ticket(100, "done", 15),
        ticket(101, "done", 1, project_dir="/other"),
        ticket(102, "cancelled", 0, proposal={"state": "declined"}),
        ticket(103, "cancelled", 0.01),
    ]
    closed = research_start.recently_closed(tickets, "/p", clock)
    assert len(closed) == research_start.CLOSED_LIMIT == 30
    assert closed[:3] == [
        {"key": "REM-102", "title": "Ticket 102", "status": "declined"},
        {"key": "REM-103", "title": "Ticket 103", "status": "cancelled"},
        {"key": "REM-1", "title": "Ticket 1", "status": "done"},
    ]
    keys = {c["key"] for c in closed}
    assert not keys & {"REM-100", "REM-101"}


def test_a_report_from_before_commits_were_recorded_starts_from_its_date():
    text = research_start.brief(
        "abc", {"report": "/r.html", "commit": "", "started_at": "2026-11-15T10:00:00+00:00"}, []
    )
    assert text == (
        "Where the last round stopped:\n- Start from the last report, /r.html, and the commits "
        "after its run of 2026-11-15 (now abc). Don't repeat what it covered."
    )
    assert research_start.brief("abc", None, []) == ""
    on_dev = research_start.brief(
        "def", {"report": "/r.html", "commit": "abc", "started_at": ""}, [], "dev"
    )
    assert "and the commits on dev after abc (now def)." in on_dev


# Text only Rem's recorded call carries; the sizes file must never hold it.
PROMPT_TEXT = "PRIVATE-BOARD-TEXT "
RULES_TEXT = "PRIVATE-RULES-TEXT "
# The path may hold spaces, so the match runs from "the file " to the name.
USAGE_FILE = re.compile(r"the file (.+?context-usage\.json) \(JSON\)")


@pytest.mark.parametrize("provider", ["codex", "claude_code"])
def test_a_research_run_reads_measured_prompt_sizes_from_its_run_folder(board, provider):
    home, fake, project = board
    home.configure(
        {
            "provider": provider,
            "model": "cli-default",
            "command_slots": {"maxWorkers": 8},
            "projects": [{"root": str(project), "name": "project"}],
        }
    )
    prompt = PROMPT_TEXT * 40
    assert record_rem_call(
        "self_work_plan",
        prompt,
        RULES_TEXT * 10,
        {"board": len(prompt)},
        config={"provider": provider},
        data_dir=home.data_dir,
    )
    seen: dict[str, Any] = {}
    original = home.source

    async def source(**kwargs: Any):
        instructions = kwargs["agent_instructions"]
        title = kwargs["workflow"]["chatThreadTitle"]
        if FEATURES in title and "independent read-only reviewer" not in instructions:
            [path] = USAGE_FILE.findall(instructions)
            file = Path(path)
            seen.update(
                path=file,
                instructions=instructions,
                text=file.read_text(encoding="utf-8"),
                mode=stat.S_IMODE(file.stat().st_mode),
            )
        async for event in original(**kwargs):
            yield event

    home.source = source
    clock = Clock(home)
    for number, title in enumerate(ANGLES):
        fake.answers[title] = ideas(f"Idea {number} a")
    window(home, clock)
    clock.to(minutes=1)
    tick(home)
    wait_for(lambda: quiet(home), 8)

    (run,) = research(home, FEATURES)
    assert run["config"]["provider"] == provider
    assert seen, "the research worker never named its context usage file"
    # The run's own private folder, which the sandbox reads and the turn removes.
    folder = run_folder(home.data_dir, run["id"], "execute")
    assert seen["path"] == folder.resolve() / CONTEXT_USAGE_FILE
    assert seen["path"].is_absolute() and str(seen["path"]) in seen["instructions"]
    assert seen["mode"] == 0o600
    usage = json.loads(seen["text"])
    assert {"parts", "rem_calls"} <= set(usage)
    assert usage["days"] == 7
    (planning,) = [c for c in usage["rem_calls"] if c["call"] == "self_work_plan"]
    assert planning["sent_chars"] >= len(prompt)
    assert set(planning) >= {"input_tokens", "cache_read_tokens", "cache_write_tokens"}
    assert any(p["id"] == "self_work_plan:board" for p in usage["parts"])
    # Sizes only: no prompt or rules text reaches the file.
    assert "PRIVATE-" not in seen["text"]
    # Scratch cleanup at the end of the turn removes it with the run's folder.
    assert not seen["path"].exists() and not folder.exists()
    # Only the research worker gets it, never its reviewer.
    reviews = [c for c in fake.calls if "independent read-only reviewer" in c["agent_instructions"]]
    assert not any(CONTEXT_USAGE_FILE in c["agent_instructions"] for c in reviews)
