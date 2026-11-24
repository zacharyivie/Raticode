"""Self-work's design and module depth research and the deepening tickets it files.

Code with no model call ranks the project's files by lines times churn over 30 days of the
landing branch's git log; the top ten go into that research angle's prompt, which asks for at
most two deepening tickets. A deepening ticket waits for open tickets that name the same files,
and self-work runs one deepening ticket at a time. Temporary git repos, fake providers and
isolated data only.
"""

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from gofer.ui import rem_design_depth as design_depth
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_kanban import new_ticket, put_ticket, read_tickets
from gofer.ui.rem_self_work import DEPTH_ANGLE, RESEARCH_ANGLES
from tests.unit.test_rem_kanban import BASE, add, at, project_of, set_options, tick, wait_for
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_self_work_fill import activity, quiet
from tests.unit.test_self_work_research_start import research

DEPTH = "Research design and module depth in project"


def git(project: Path, *args: str, when: datetime | None = None) -> str:
    env = dict(os.environ)
    if when is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when.isoformat()
    return subprocess.run(
        ["git", "-C", str(project), "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout.strip()


def change(project: Path, path: str, lines: int, when: datetime, salt: str) -> None:
    """Commit ``path`` rewritten to ``lines`` lines at ``when``."""
    file = project / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("".join(f"{salt} line {n}\n" for n in range(lines)))
    git(project, "add", path)
    git(project, "commit", "-q", "--no-verify", "-m", f"{path} {salt}", when=when)


def history(project: Path, now: datetime) -> None:
    """A repo where the large, often-changed file should rank first.

    hot.py: 200 lines x 6 commits; big.py: 1000 lines x 1 commit; small.py: 10 lines x 8
    commits; old.py: huge but last changed 60 days ago; gone.py: busy but deleted since.
    """
    git(project, "init", "-q", "-b", "main")
    change(project, "src/old.py", 5000, now - timedelta(days=60), "old")
    for n in range(6):
        change(project, "src/hot.py", 200, now - timedelta(days=20 - n), f"hot{n}")
    change(project, "src/big.py", 1000, now - timedelta(days=10), "big")
    for n in range(8):
        change(project, "src/small.py", 10, now - timedelta(days=9 - n), f"small{n}")
    for n in range(9):
        change(project, "src/gone.py", 3000, now - timedelta(days=8, hours=n), f"gone{n}")
    git(project, "rm", "-q", "src/gone.py")
    git(project, "commit", "-q", "--no-verify", "-m", "drop gone", when=now - timedelta(days=1))


def test_research_angles_include_design_and_module_depth():
    assert RESEARCH_ANGLES[DEPTH_ANGLE][0] == "Research design and module depth in {name}"
    assert len(RESEARCH_ANGLES) == 4


def test_churn_ranking_puts_the_large_often_changed_file_first(tmp_path):
    project = tmp_path / "repo"
    project.mkdir()
    now = datetime.now(UTC)
    history(project, now)
    tip = git(project, "rev-parse", "HEAD")

    rows = design_depth.hotspots(str(project), tip, now)

    assert [r["path"] for r in rows] == ["src/hot.py", "src/big.py", "src/small.py"]
    hot = rows[0]
    assert (hot["lines"], hot["commits"], hot["score"]) == (200, 6, 1200)
    assert hot["changed"] == 200 + 5 * 400  # created, then five rewrites
    # Older than 30 days, or deleted at the tip, it isn't ranked.
    assert all(r["path"] not in {"src/old.py", "src/gone.py"} for r in rows)
    # No more than ten, and nothing for a folder outside git or an empty commit.
    assert len(design_depth.hotspots(str(project), tip, now, limit=1)) == 1
    assert design_depth.hotspots(str(tmp_path / "nowhere"), tip, now) == []
    assert design_depth.hotspots(str(project), "", now) == []


def test_the_design_angle_prompt_has_the_ranked_table_and_two_proposals_at_most(board):
    home, fake, project = board
    history(project, BASE)
    set_options(home, max_threads=4)
    at(home, 15)
    tick(home)
    wait_for(lambda: quiet(home), 8)

    (run,) = research(home, DEPTH)
    prompt = run["task"]["prompt"]
    assert run["self_work"]["angle"] == DEPTH_ANGLE
    table = design_depth.table(
        design_depth.hotspots(str(project), run["self_work"]["commit"], BASE)
    )
    assert table in prompt
    assert "| 1 | src/hot.py | 200 | 6 | 2200 | 1200 |" in prompt
    assert "src/old.py" not in prompt
    assert "deletion test" in prompt and "inlined into its callers" in prompt
    assert "Propose at most 2 deepening tickets" in prompt
    assert "before and after sketch of the interface" in prompt
    assert design_depth.FINISH_LINE in prompt
    # The other angles don't get the table.
    other = research(home, "Research new features for project")[0]
    assert "src/hot.py" not in other["task"]["prompt"]


def deepening_answer(*proposals: tuple[str, str]) -> str:
    body = ",".join(
        json.dumps(
            {
                "title": title,
                "description": description,
                "why": "Callers repeat its setup",
                "done_when": ["pytest tests/unit/test_hot.py passes"],
            }
        )
        for title, description in proposals
    )
    return 'Done.\n```json\n{"proposals":[' + body + '],"duplicates":[]}\n```'


def test_deepening_proposals_wait_for_tickets_on_the_same_files_and_stop_at_two(board):
    home, fake, project = board
    history(project, BASE)
    add(home, "Fix the crash in hot.py on empty input", status="backlog")
    add(home, "Rename the settings page", status="backlog")
    fake.answers[DEPTH] = deepening_answer(
        ("Deepen the hot module", "Before: open(), read(), close() in src/hot.py. After: load()."),
        ("Fold small helpers into big", "Merge src/small.py into src/big.py behind one call."),
        ("A third deepening", "Change src/hot.py again."),
    )
    set_options(home, max_threads=4)
    at(home, 15)
    tick(home)
    wait_for(lambda: quiet(home), 8)

    with home.store.read() as db:
        tickets = {t["title"]: t for t in read_tickets(db)}
    assert "A third deepening" not in tickets
    hot, small = tickets["Deepen the hot module"], tickets["Fold small helpers into big"]
    same = tickets["Fix the crash in hot.py on empty input"]
    unrelated = tickets["Rename the settings page"]
    assert hot["deepening"]["files"] == ["src/hot.py"]
    assert small["deepening"]["files"] == ["src/big.py", "src/small.py"]
    # It waits for the open ticket that names the same file, not the unrelated one.
    assert hot["waits_for"] == [same["id"]]
    assert unrelated["id"] not in hot["waits_for"] + small["waits_for"]
    assert small["waits_for"] == []
    for ticket in (hot, small):
        assert ticket["done_when"].splitlines() == [
            "pytest tests/unit/test_hot.py passes",
            design_depth.FINISH_LINE,
        ]
        assert ticket["status"] == "backlog" and ticket["proposal"]["state"] == "pending"


def put_deepening(home: RemHome, title: str, **fields: Any) -> dict[str, Any]:
    with home.store.connect() as db:
        created = new_ticket(
            db,
            project_dir=project_of(home),
            title=title,
            status="todo",
            created_by="self_work",
            deepening={"files": ["src/hot.py"]},
        )
        created.update(fields)
        put_ticket(db, created)
    return created


def status(home: RemHome, title: str) -> str:
    with home.store.read() as db:
        return next(str(t["status"]) for t in read_tickets(db) if t["title"] == title)


def test_a_deepening_ticket_is_not_started_while_another_is_in_progress(board):
    home, fake, _ = board
    put_deepening(home, "Deepen one")
    put_deepening(home, "Deepen two")
    add(home, "Ordinary todo")
    fake.hold.clear()
    set_options(home, max_threads=4)
    at(home, 15)
    tick(home)
    running = activity(home)
    # One deepening ticket runs; the other waits though threads are free, and other work fills
    # them.
    assert sum(title.startswith("Deepen") for title in running) == 1
    assert "Ordinary todo" in running and len(running) == 4
    first = next(t for t in running if t.startswith("Deepen"))
    second = "Deepen two" if first == "Deepen one" else "Deepen one"
    assert status(home, first) == "in_progress" and status(home, second) == "todo"
    for _ in range(3):
        tick(home)
    assert status(home, second) == "todo"
    # Once the first is finished, the second starts. Held research passes may take the
    # thread the first frees while it is still in review, so let research finish too.
    fake.released.update(title.format(name="project") for title, _ in RESEARCH_ANGLES)
    fake.released.add(first)
    wait_for(lambda: not tick(home) and status(home, first) == "done", 8)
    wait_for(lambda: not tick(home) and status(home, second) != "todo", 8)
    assert status(home, second) in {"in_progress", "in_review", "done"}
    fake.hold.set()
