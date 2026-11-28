"""Ask Rem: the button in a project's Objectives section that has Rem write objectives for it.

Rem's tool-free model reads the project (docs, folders, commits, open tickets, open objectives)
and writes three to five broad, open-ended objectives for it, at most five a press. They are
the user's request, so they may stay open-ended, while Rem's own autonomous writing keeps its
rules. Fake providers and isolated data only.
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import rem_objective_ideas as ideas
from gofer.ui import rem_objectives as objectives
from gofer.ui.headless import agent_request_refusal
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_home_provider import ProviderFailure
from tests.unit.test_rem_kanban import add, api_server, project_of
from tests.unit.test_rem_kanban import board as board

assert api_server  # a fixture, imported for the HTTP test below

BROAD = [
    "Improve UX across the project's features",
    "Audit unit tests and rewrite or remove unhelpful ones",
    "Harden error handling and recovery in the sync code",
    "Bring the docs in line with what the app does today",
    "Simplify the build and release scripts",
]
OPEN = "Raise reliability of the storage layer"
UX = "Raise accessibility of every screen"


def asking(home: RemHome, answers: list[Any]) -> list[str]:
    """Rem's model: answers Ask Rem's calls with ``answers`` in turn. Returns the prompts."""
    prompts: list[str] = []
    usual = home.generate

    def generate(prompt: str, config: Any, cancel: Any) -> Any:
        if not prompt.startswith(ideas.RULES):
            return usual(prompt, config, cancel)
        prompts.append(prompt)
        answer = answers[min(len(prompts), len(answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    home.generate = generate
    return prompts


def finished(home: RemHome) -> dict[str, Any]:
    root = project_of(home)
    thread = home.self_work.ideas._threads.get(root)
    if thread is not None:
        thread.join(timeout=10)
    return home.self_work.ideas.view()[root]


def own(home: RemHome) -> list[dict[str, Any]]:
    return list(home.self_work.objectives(project=project_of(home))["items"])


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.invalid", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def test_whole_session_goals_pass_and_narrow_ones_are_refused():
    for text in BROAD:
        assert ideas.breadth_problem(text) == "", text
    for text in (
        "Fix the login bug",
        "Add a dark mode toggle",
        "Rename the settings page",
        "Upgrade React to version 20",
        "Improve REM-12 handling",
        "Speed up main.py",
        "Improve tests",
    ):
        assert ideas.breadth_problem(text), text


def test_rules_ask_for_broad_open_ended_goals_for_a_whole_session():
    rules = ideas.RULES
    assert "whole session" in rules and "several dozen tickets" in rules
    assert "Improve UX across the project's features" in rules
    assert "Audit unit tests and rewrite or remove unhelpful ones" in rules
    assert "leave done_when out" in rules
    assert f"{ideas.FEWEST} to {ideas.MOST} objectives" in rules
    # Not the limit of Rem's own: the user asked for these.
    assert "at most six open objectives of your own" not in rules


def test_ask_rem_reads_the_project_and_adds_up_to_five_open_ended_objectives(board):
    home, _, project = board
    root = project_of(home)
    (project / "README.md").write_text("# Tide\nA tide-chart app for sailors.\n")
    (project / "src").mkdir()
    (project / "src" / "charts.py").write_text("print('tides')\n")
    git(project, "init", "-q")
    git(project, "add", ".")
    git(project, "commit", "-q", "-m", "Draw the harbour tide chart")
    add(home, "Chart labels overlap on small screens")
    with home.store.connect() as db:
        objectives.change(
            db, home.store, {"action": "add", "text": OPEN, "project": root}, actor="user"
        )
    # The first answer repeats an open objective and has one task, so it is asked to repair
    # once; the repair names six broad ones, of which five are saved.
    prompts = asking(
        home,
        [
            {"objectives": [{"text": OPEN}, {"text": "Fix the chart label overlap"}]},
            {
                "objectives": [
                    {"text": t, "done_when": "ignored"}
                    for t in [*BROAD, "Raise accessibility of every screen"]
                ]
            },
        ],
    )
    started = home.self_work.ask_objectives({"project": root})
    assert started["asking"][root]["state"] == "asking"
    outcome = finished(home)
    assert outcome["state"] == "added" and outcome["added"] == 5
    assert outcome["message"] == "Added 5 objectives for project."

    records = json.loads(prompts[0].split("Records:\n", 1)[1])
    assert "tide-chart app" in records["docs"]["README.md"]
    assert records["recent_commits"] == ["Draw the harbour tide chart"]
    assert {"folder": "src", "files": 1} in records["folders"]
    assert records["open_tickets"] == [
        {"title": "Chart labels overlap on small screens", "status": "todo"}
    ]
    assert records["open_objectives"] == [{"text": OPEN, "scope": "this project"}]
    assert len(prompts) == 2 and "Problems:" in prompts[1]
    assert "already open" in prompts[1] and "reads as one task" in prompts[1]

    added = [i for i in own(home) if i["text"] != OPEN]
    assert [i["text"] for i in added] == BROAD
    for item in added:
        assert item["project"] == root and item["done_when"] == ""
        assert (item["created_by"], item["source"]) == ("rem", "ask")
        # They are the user's open-ended ones: no planner ever gives them a finish line.
        assert not objectives.needs_finish_line(item)
    # They don't touch the global list.
    assert home.self_work.objectives()["items"] == []


def test_the_button_works_while_rems_autonomous_rules_still_refuse(board):
    """Ask Rem adds open-ended objectives; self-work on its own still can't."""
    home, _, _ = board
    root = project_of(home)
    asking(home, [{"objectives": [{"text": t} for t in BROAD[:3]]}])
    home.self_work.ask_objectives({"project": root})
    assert finished(home)["state"] == "added"
    assert len(own(home)) == 3
    for source in ("self_work", "own"):
        with home.store.connect() as db, pytest.raises(objectives.ObjectiveRefused) as refused:
            objectives.change(
                db,
                home.store,
                {"action": "add", "text": BROAD[4], "project": root},
                actor="rem",
                source=source,
            )
        assert refused.value.code == "unauthorized" and "still open" in str(refused.value)
        # Even with a finish line: on its own, Rem adds only while no objective is open.
        with home.store.connect() as db, pytest.raises(objectives.ObjectiveRefused) as refused:
            objectives.change(
                db,
                home.store,
                {
                    "action": "add",
                    "text": BROAD[4],
                    "done_when": "Every release script has a test",
                    "project": root,
                },
                actor="rem",
                source=source,
            )
        assert refused.value.code == "unauthorized"
        assert "On its own, Rem adds objectives only when none are open" in str(refused.value)
    # Pressing Ask Rem again still works while those are open.
    asking(home, [{"objectives": [{"text": BROAD[3]}, {"text": BROAD[4]}, {"text": UX}]}])
    home.self_work.ask_objectives({"project": root})
    assert finished(home)["added"] == 3
    assert len(own(home)) == 6
    # Rem on its own can't turn the user's asked-for objectives into its own either.
    item = own(home)[0]
    with home.store.connect() as db, pytest.raises(objectives.ObjectiveRefused) as refused:
        objectives.change(
            db,
            home.store,
            {"action": "edit", "id": item["id"], "done_when": "All tests pass in CI"},
            actor="rem",
            source="self_work",
        )
    assert refused.value.code == "unauthorized"
    assert len(own(home)) == 6


def test_asked_objectives_dont_count_toward_the_few_rem_keeps_of_its_own(board):
    home, _, _ = board
    root = project_of(home)
    # One batch Rem wrote for an empty list, saved one at a time with one stamp.
    batch = objectives.now()
    with home.store.connect() as db:
        for n in range(objectives.MAX_OPEN_BY_REM):
            objectives.change(
                db,
                home.store,
                {
                    "action": "add",
                    "text": f"Improve area number {n}",
                    "done_when": "Every screen loads in a second",
                    "project": root,
                },
                actor="rem",
                source="self_work",
                stamp=batch,
            )
    asking(home, [{"objectives": [{"text": t} for t in BROAD]}])
    home.self_work.ask_objectives({"project": root})
    assert finished(home)["added"] == 5
    assert len(own(home)) == objectives.MAX_OPEN_BY_REM + 5


def test_a_press_is_capped_by_the_room_left_in_the_list(board):
    home, _, _ = board
    root = project_of(home)
    with home.store.connect() as db:
        for n in range(objectives.MAX_ITEMS - 2):
            objectives.change(
                db,
                home.store,
                {"action": "add", "text": f"Goal number {n} here", "project": root},
                actor="user",
            )
    asking(home, [{"objectives": [{"text": t} for t in BROAD]}])
    home.self_work.ask_objectives({"project": root})
    assert finished(home)["added"] == 2
    # Now full: a press is refused before Rem is asked.
    with pytest.raises(objectives.ObjectiveRefused) as full:
        home.self_work.ask_objectives({"project": root})
    assert full.value.code == "invalid" and "30 open objectives" in str(full.value)


def test_failures_and_presses_already_under_way_show_their_reason(board):
    home, _, _ = board
    root = project_of(home)
    asking(home, [ProviderFailure("codex", "You've hit your usage limit.")])
    home.self_work.ask_objectives({"project": root})
    outcome = finished(home)
    assert outcome["state"] == "failed"
    assert "usage limit" in outcome["message"] and own(home) == []
    # Nothing usable twice: the user is told to try again, and nothing is saved.
    asking(home, [{"objectives": [{"text": "Fix the login bug"}]}])
    home.self_work.ask_objectives({"project": root})
    assert finished(home)["message"].startswith("Rem found no new objectives for project")
    assert own(home) == []

    gate = threading.Event()

    def slow(prompt: str, config: Any, cancel: Any) -> Any:
        gate.wait(5)
        return answer(prompt, config, cancel)

    asking(home, [{"objectives": [{"text": t} for t in BROAD[:3]]}])
    answer = home.generate
    home.generate = slow
    home.self_work.ask_objectives({"project": root})
    with pytest.raises(objectives.ObjectiveRefused) as twice:
        home.self_work.ask_objectives({"project": root})
    assert twice.value.code == "conflict"
    gate.set()
    assert finished(home)["state"] == "added"
    with pytest.raises(objectives.ObjectiveRefused):
        home.self_work.ask_objectives({"project": "/somewhere/else"})
    with pytest.raises(objectives.ObjectiveRefused):
        home.self_work.ask_objectives({"project": ""})


def test_the_side_pane_list_shows_ask_progress_as_it_changes(board):
    home, _, _ = board
    root = project_of(home)
    gate = threading.Event()

    def slow(prompt: str, config: Any, cancel: Any) -> Any:
        gate.wait(5)
        return answer(prompt, config, cancel)

    asking(home, [{"objectives": [{"text": t} for t in BROAD[:3]]}])
    answer = home.generate
    home.generate = slow
    _, before = home.task_list.view()
    home.self_work.ask_objectives({"project": root})
    listed, during = home.task_list.view()
    assert during != before and listed["objectives"]["asking"][root]["state"] == "asking"
    gate.set()
    finished(home)
    listed, after = home.task_list.view()
    assert after != during and listed["objectives"]["asking"][root]["state"] == "added"
    assert home.self_work.view()["objectives"]["asking"][root]["added"] == 3


def test_the_route_starts_an_ask_and_only_the_user_may(api_server):
    request, service = api_server
    refused = request("POST", "/api/rem/objectives/ask", body={"project": "/not/rems"})
    assert refused.status == 400 and refused.json()["code"] == "invalid"
    assert request("POST", "/api/rem/objectives/ask", body={}).status == 400
    assert agent_request_refusal("POST", "/api/rem/objectives/ask")
