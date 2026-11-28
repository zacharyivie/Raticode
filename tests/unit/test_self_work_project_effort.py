"""Each of Rem's projects' level of effort and its own objectives in self-work.

The level of effort is a project's share of self-work's threads: with a thread limit of 10 and
70/30, the first project holds 7 threads and the second 3, however many tasks each finishes.
A project with nothing to start lends its threads to the others and gets them back as they
free. A project's own objectives steer only that project's tickets and research.
"""

import time
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import rem_objective_fit as objective_fit
from gofer.ui import rem_objectives as objectives
from gofer.ui import rem_self_work_effort as effort
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_kanban import KanbanRefused
from tests.unit.test_rem_kanban import add, add_proposal, at, set_options, tick, wait_for
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_self_work_objectives import (
    change,
    checker,
    decide_objectives,
    step,
    stored,
)


def trading_project(home: RemHome, project: Path, workers: int = 10) -> str:
    """Add a second project, agent-trading, to Rem's projects; returns its folder."""
    trading = project.parent / "agent-trading"
    trading.mkdir()
    home.configure(
        {
            "provider": "codex",
            "model": "cli-default",
            "command_slots": {"maxWorkers": workers},
            "projects": [
                {"root": str(project), "name": "project"},
                {"root": str(trading), "name": "agent-trading"},
            ],
        }
    )
    home.kanban.set_participant_projects(
        "user",
        {"participant_id": "rem", "projects": [str(project), str(trading)], "expected_revision": 1},
    )
    return str(trading)


def threads(home: RemHome) -> dict[str, tuple[int, int]]:
    """Each project's (running, target) threads, by name, as Settings > Rem shows them."""
    return {
        row["name"]: (row["running_threads"], row["target_threads"])
        for row in home.self_work.view()["projects"]
    }


def no_research(home: RemHome) -> None:
    """Twelve ideas waiting for approval stop research, so only tickets take threads."""
    for number in range(12):
        add_proposal(home, f"Idea {number}")


# The numbers ------------------------------------------------------------------------------


def test_levels_of_effort_default_to_an_even_split_that_totals_100():
    assert effort.percents(["a", "b"], {}) == {"a": 50, "b": 50}
    assert effort.percents(["a", "b", "c"], {}) == {"a": 34, "b": 33, "c": 33}
    # A project with no number yet gets an even share and the others scale around it.
    assert effort.percents(["a", "b", "c"], {"a": 70, "b": 30}) == {"a": 47, "b": 20, "c": 33}
    # A disabled project's number is kept but doesn't count.
    assert effort.percents(["a"], {"a": 70, "b": 30}) == {"a": 100}
    assert effort.percents([], {"a": 70}) == {}


def test_setting_some_projects_rescales_the_others_to_fill_the_rest():
    roots = ["a", "b", "c"]
    stored = effort.change(roots, {}, {"a": 70})
    assert effort.percents(roots, stored) == {"a": 70, "b": 15, "c": 15}
    stored = effort.change(roots, stored, {"b": 25})
    assert effort.percents(roots, stored) == {"a": 62, "b": 25, "c": 13}
    for given in ({"a": 101}, {"a": 60, "b": 50}, {"zzz": 10}, {"a": "70"}, {}):
        with pytest.raises(effort.EffortRefused):
            effort.change(roots, stored, given)
    with pytest.raises(effort.EffortRefused, match="add up to 100"):
        effort.change(["a", "b"], {}, {"a": 60, "b": 30})


def test_threads_follow_the_shares_with_rounding_left_to_the_closest():
    assert effort.targets({"raticode": 70, "trading": 30}, 10) == {"raticode": 7, "trading": 3}
    assert effort.targets({"raticode": 70, "trading": 30}, 3) == {"raticode": 2, "trading": 1}
    # Equally close: the larger share gets the thread.
    assert effort.targets({"raticode": 50, "trading": 50}, 3) == {"raticode": 2, "trading": 1}
    assert effort.targets({"raticode": 60, "trading": 40}, 5) == {"raticode": 3, "trading": 2}
    assert effort.targets({"a": 34, "b": 33, "c": 33}, 3) == {"a": 1, "b": 1, "c": 1}
    assert effort.targets({"a": 100, "b": 0}, 4) == {"a": 4, "b": 0}


# The scheduler ----------------------------------------------------------------------------


def test_slots_stay_at_seven_and_three_however_many_short_tasks_finish(board):
    """70/30 of 10 threads: Raticode churns through short tickets in its 7 threads while
    agent-trading's 3 long ones keep running, and agent-trading never gets more than 3 even
    with more of its tickets waiting."""
    home, fake, project = board
    trading = trading_project(home, project)
    no_research(home)
    set_options(home, max_threads=10, project_effort={str(project): 70, trading: 30})
    fake.hold.clear()
    for number in range(5):
        add(home, f"Long trading task {number}", status="todo", project_dir=trading)
    quick = [f"Quick fix {number}" for number in range(20)]
    for title in quick:
        add(home, title, status="todo")
    at(home, 15)
    tick(home)
    assert threads(home) == {"project": (7, 7), "agent-trading": (3, 3)}
    # Raticode's short tickets finish one after another; its threads refill from its own
    # To do, and agent-trading still holds exactly its 3.
    fake.released.update(quick)
    seen = 0
    deadline = time.monotonic() + 20
    while seen < 12 and time.monotonic() < deadline:
        tick(home)
        now = threads(home)
        assert now["agent-trading"] == (3, 3)
        assert now["project"][0] <= 7
        seen = sum(1 for title in quick if stored(home, title)["status"] == "done")
        time.sleep(0.02)
    assert seen >= 12, "Raticode finished many short tasks in its own threads"
    long_running = [stored(home, f"Long trading task {number}")["status"] for number in range(5)]
    assert long_running.count("in_progress") == 3 and long_running.count("todo") == 2
    line = home.self_work.view()["state"]["detail"]
    assert "Threads by project: project 7 of 7, agent-trading 3 of 3." in line


def test_a_project_with_nothing_to_start_lends_its_threads_and_gets_them_back(board):
    home, fake, project = board
    trading = trading_project(home, project)
    no_research(home)
    set_options(home, max_threads=10, project_effort={str(project): 70, trading: 30})
    fake.hold.clear()
    for number in range(12):
        add(home, f"Raticode task {number}", status="todo")
    at(home, 15)
    tick(home)
    # agent-trading has nothing to start, so Raticode borrows its 3 threads.
    assert threads(home) == {"project": (10, 7), "agent-trading": (0, 3)}
    # Work appears in agent-trading: the next thread that frees goes to it, not Raticode.
    add(home, "Trading task", status="todo", project_dir=trading)
    tick(home)
    assert stored(home, "Trading task")["status"] == "todo", "nothing running is stopped"
    fake.released.add("Raticode task 0")
    wait_for(lambda: not tick(home) and stored(home, "Trading task")["status"] != "todo", 8)
    assert stored(home, "Trading task")["status"] == "in_progress"
    running = threads(home)
    assert running["agent-trading"] == (1, 3) and running["project"][0] == 9


def test_the_user_sets_levels_of_effort_for_rems_projects(board):
    home, _, project = board
    trading = trading_project(home, project)
    view = home.self_work.view()
    assert view["settings"]["project_effort"] == {str(project): 50, trading: 50}
    assert [row["name"] for row in view["projects"]] == ["project", "agent-trading"]
    set_options(home, project_effort={trading: 30})
    assert home.self_work.view()["settings"]["project_effort"] == {str(project): 70, trading: 30}
    with pytest.raises(KanbanRefused, match="isn't one of Rem's projects"):
        set_options(home, project_effort={"/not/a/project": 10})
    # Taking a project off Rem's projects keeps its number for when it comes back.
    home.kanban.set_participant_projects(
        "user", {"participant_id": "rem", "projects": [str(project)], "expected_revision": 2}
    )
    assert home.self_work.view()["settings"]["project_effort"] == {str(project): 100}
    home.kanban.set_participant_projects(
        "user",
        {"participant_id": "rem", "projects": [str(project), trading], "expected_revision": 3},
    )
    assert home.self_work.view()["settings"]["project_effort"] == {str(project): 70, trading: 30}


# Each project's own objectives ------------------------------------------------------------

TRADING_GOAL = "Raise the trading strategy's risk-adjusted returns"
GLOBAL_GOAL = "Improve reliability of the Second Brain tools"


def test_a_project_objective_never_pulls_another_projects_ticket(board):
    """A Raticode ticket the model ties to agent-trading's objective stays in To do; only
    agent-trading's tickets advance it."""
    home, fake, project = board
    trading = trading_project(home, project)
    change(home, {"action": "add", "text": GLOBAL_GOAL}, actor="user")
    change(home, {"action": "add", "text": TRADING_GOAL, "project": trading}, actor="user")
    fake.hold.clear()
    checker(
        home,
        {"Tune the trading strategy here": TRADING_GOAL, "Backtest the strategy": TRADING_GOAL},
    )
    add(home, "Tune the trading strategy here", status="todo")
    add(home, "Backtest the strategy", status="todo", project_dir=trading)
    at(home, 15)
    step(home)
    wait_for(lambda: not tick(home) and stored(home, "Backtest the strategy")["claim"], 8)
    raticode = stored(home, "Tune the trading strategy here")
    assert raticode["status"] == "todo" and not raticode.get("objective_id")
    listed = home.self_work.objectives()
    fit = objective_fit.judge(raticode, _all(home))
    assert fit[0] == "unrelated"
    goal = listed["projects"][trading]["items"][0]
    assert goal["text"] == TRADING_GOAL
    assert stored(home, "Backtest the strategy")["objective_id"] == goal["id"]
    # Research for agent-trading's objective runs only in agent-trading.
    research = [r for r in home.self_work.view()["state"]["activity"] if TRADING_GOAL in r["title"]]
    assert all(r["project"] == "agent-trading" for r in research)


def _all(home: RemHome) -> dict[str, Any]:
    with home.store.read() as db:
        return objectives.read(db, home.store)


def test_each_project_lists_its_own_objectives_and_set_replaces_only_its_own(board):
    home, _, project = board
    trading = trading_project(home, project)
    change(home, {"action": "add", "text": GLOBAL_GOAL}, actor="user")
    home.self_work.change_objectives(
        {"action": "add", "text": TRADING_GOAL, "project": "agent-trading"}
    )
    view = home.self_work.objectives()
    assert [i["text"] for i in view["items"]] == [GLOBAL_GOAL]
    assert [i["text"] for i in view["projects"][trading]["items"]] == [TRADING_GOAL]
    assert home.self_work.objectives(project=trading)["items"][0]["project"] == trading
    # Rem in chat, naming the project: only agent-trading's open objectives are replaced.
    decide_objectives(
        home,
        "For agent-trading, focus on cutting drawdowns instead",
        {
            "action": "set",
            "project": "agent-trading",
            "items": [{"text": "Cut the trading strategy's drawdowns"}],
        },
    )
    view = home.self_work.objectives()
    assert [i["text"] for i in view["items"]] == [GLOBAL_GOAL]
    assert [i["text"] for i in view["projects"][trading]["items"]] == [
        "Cut the trading strategy's drawdowns"
    ]
    with pytest.raises(objectives.ObjectiveRefused, match="isn't one of Rem's projects"):
        home.self_work.change_objectives(
            {"action": "add", "text": TRADING_GOAL, "project": "nowhere"}
        )
    # A task self-work starts in Raticode is never briefed on agent-trading's objectives.
    brief = objectives.brief(objectives.applying(_all(home), str(project)))
    assert GLOBAL_GOAL in brief and "drawdowns" not in brief


def test_edit_moves_an_objective_between_the_global_list_and_a_project_and_back(board):
    home, _, project = board
    trading = trading_project(home, project)
    change(home, {"action": "add", "text": GLOBAL_GOAL}, actor="user")
    goal = home.self_work.objectives()["items"][0]
    # Into agent-trading, named as Rem's projects name it: same id, author and source.
    view = home.self_work.change_objectives(
        {"action": "edit", "id": goal["id"], "project": "agent-trading"}
    )
    assert view["items"] == []
    moved = home.self_work.objectives()["projects"][trading]["items"][0]
    assert moved["project"] == trading
    assert {k: moved[k] for k in ("id", "text", "created_by", "source")} == {
        k: goal[k] for k in ("id", "text", "created_by", "source")
    }
    # Rem's chat edits change the words and leave the objective where it is.
    decide_objectives(
        home,
        "Reword the Second Brain objective",
        {"action": "edit", "id": goal["id"], "text": "Improve every Second Brain tool"},
    )
    reworded = home.self_work.objectives()["projects"][trading]["items"][0]
    assert (reworded["id"], reworded["text"]) == (goal["id"], "Improve every Second Brain tool")
    # And back to the global ones with "".
    view = home.self_work.change_objectives({"action": "edit", "id": goal["id"], "project": ""})
    back = view["items"][0]
    assert (back["id"], back["created_by"], "project" in back) == (goal["id"], "user", False)
    assert trading not in view["projects"]
    # agent-trading already has the same open text: refused, and it stays global.
    change(
        home,
        {"action": "add", "text": "IMPROVE every Second Brain tool!", "project": trading},
        actor="user",
    )
    with pytest.raises(objectives.ObjectiveRefused, match="already on the list") as refused:
        home.self_work.change_objectives(
            {"action": "edit", "id": goal["id"], "done_when": "Not saved", "project": trading}
        )
    assert refused.value.code == "conflict"
    kept = home.self_work.objectives()["items"][0]
    assert (kept["id"], kept["done_when"], "project" in kept) == (goal["id"], "", False)
    # A full project list refuses the move the way adding to it would.
    for number in range(objectives.MAX_ITEMS - 1):
        change(
            home,
            {"action": "add", "text": f"Trading goal {number}", "project": trading},
            actor="user",
        )
    reworded = {"action": "edit", "id": goal["id"], "text": "A new wording", "project": trading}
    with pytest.raises(objectives.ObjectiveRefused, match="at most 30 open objectives"):
        home.self_work.change_objectives(reworded)
    kept = home.self_work.objectives()["items"][0]
    assert (kept["id"], kept["text"]) == (goal["id"], "Improve every Second Brain tool")
    # A project that isn't one of Rem's is refused before anything changes.
    with pytest.raises(objectives.ObjectiveRefused, match="isn't one of Rem's projects"):
        home.self_work.change_objectives({"action": "edit", "id": goal["id"], "project": "nowhere"})
