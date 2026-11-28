"""The phone reads and sets each of Rem's projects' level of effort and own objectives.

It names projects by their catalog ID, as on the board, and never sees a folder path.
"""

import json
from typing import Any

from tests.unit.test_device_application import application as application
from tests.unit.test_device_desktop_control import phone as phone
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_rem_kanban import linked as linked
from tests.unit.test_rem_kanban import org as org

assert application and phone and board and org  # fixtures, imported for linked

GOAL = "Raise the trading strategy's risk-adjusted returns"


def rem_projects(home: Any, context: dict[str, Any]) -> list[dict[str, str]]:
    """Make the phone's two shared projects Rem's projects; returns them with their IDs."""
    shared = [{"root": p["root"], "name": p["name"], "id": p["id"]} for p in context["projects"]]
    home.configure(
        {
            "provider": "codex",
            "model": "cli-default",
            "command_slots": {"maxWorkers": 8},
            "projects": [{"root": p["root"], "name": p["name"]} for p in shared],
        }
    )
    home.kanban.set_participant_projects(
        "user",
        {
            "participant_id": "rem",
            "projects": [p["root"] for p in shared],
            "expected_revision": 1,
        },
    )
    return shared


def test_phone_sets_levels_of_effort_and_project_objectives_by_project_id(linked):
    _, _, context, send, home, _, _, _ = linked
    first, second = rem_projects(home, context)
    view = send("desktop.read", "selfwork.read")["value"]
    assert first["root"] not in json.dumps(view) and second["root"] not in json.dumps(view)
    assert [row["project_id"] for row in view["projects"]] == [first["id"], second["id"]]
    assert view["settings"]["project_effort"] == {first["id"]: 50, second["id"]: 50}

    changed = send(
        "desktop.action",
        "selfwork.settings",
        {
            "project_effort": {second["id"]: 30},
            "expected_revision": view["settings"]["revision"],
        },
    )
    assert changed["ok"], changed
    assert changed["value"]["settings"]["project_effort"] == {first["id"]: 70, second["id"]: 30}
    assert home.self_work.view()["settings"]["project_effort"] == {
        first["root"]: 70,
        second["root"]: 30,
    }

    added = send(
        "desktop.action",
        "selfwork.objectives",
        {
            "action": "add",
            "text": GOAL,
            "project_id": second["id"],
            "expected_revision": view["objectives"]["revision"],
        },
    )
    assert added["ok"], added
    objectives = added["value"]["objectives"]
    assert second["root"] not in json.dumps(added["value"])
    [item] = objectives["projects"][second["id"]]["items"]
    assert item["text"] == GOAL and item["project_id"] == second["id"]
    assert objectives["items"] == []
    assert home.self_work.objectives(project=second["root"])["items"][0]["text"] == GOAL

    checked = send(
        "desktop.action",
        "selfwork.objectives",
        {"action": "check", "id": item["id"], "expected_revision": objectives["revision"]},
    )
    assert checked["value"]["objectives"]["projects"][second["id"]]["items"][0]["done"] is True


def test_phone_objective_changes_are_refused_with_the_desktops_words(linked):
    _, _, context, send, home, _, _, _ = linked
    rem_projects(home, context)
    revision = send("desktop.read", "selfwork.read")["value"]["objectives"]["revision"]
    stale = send(
        "desktop.action",
        "selfwork.objectives",
        {"action": "add", "text": GOAL, "expected_revision": revision + 5},
    )
    assert stale["code"] == "conflict"
    gone = send(
        "desktop.action",
        "selfwork.objectives",
        {"action": "remove", "id": "nothere", "expected_revision": revision},
    )
    assert gone["code"] == "not_found"
    unknown = send(
        "desktop.action",
        "selfwork.objectives",
        {
            "action": "add",
            "text": GOAL,
            "project_id": "6f1c2a8e-3b4d-5e6f-8a9b-0c1d2e3f4a5b",
        },
    )
    assert unknown["code"] == "invalid"
    # A folder is never taken from the phone.
    folder = send(
        "desktop.action",
        "selfwork.objectives",
        {"action": "add", "text": GOAL, "project": "/home/me/site"},
    )
    assert folder["ok"] is False
    effort = send(
        "desktop.action",
        "selfwork.settings",
        {"project_effort": {"/home/me/site": 10}, "expected_revision": 0},
    )
    assert effort["ok"] is False


def test_phone_edit_moves_an_objective_to_a_project_and_back_to_the_global_ones(linked):
    _, _, context, send, home, _, _, _ = linked
    _, second = rem_projects(home, context)
    view = send("desktop.read", "selfwork.read")["value"]
    assert view["objectives"]["movable"] is True
    added = send(
        "desktop.action",
        "selfwork.objectives",
        {"action": "add", "text": GOAL, "expected_revision": view["objectives"]["revision"]},
    )["value"]["objectives"]
    [goal] = added["items"]
    moved = send(
        "desktop.action",
        "selfwork.objectives",
        {
            "action": "edit",
            "id": goal["id"],
            "text": GOAL,
            "done_when": "Sharpe above 1.5 for a month",
            "project_id": second["id"],
            "expected_revision": added["revision"],
        },
    )
    assert moved["ok"], moved
    listed = moved["value"]["objectives"]
    assert listed["items"] == []
    [there] = listed["projects"][second["id"]]["items"]
    assert (there["id"], there["done_when"]) == (goal["id"], "Sharpe above 1.5 for a month")
    back = send(
        "desktop.action",
        "selfwork.objectives",
        {
            "action": "edit",
            "id": goal["id"],
            "global": True,
            "expected_revision": listed["revision"],
        },
    )
    assert back["ok"], back
    assert back["value"]["objectives"]["items"][0]["id"] == goal["id"]
    assert second["id"] not in back["value"]["objectives"]["projects"]
    # Without either field the objective stays where it is.
    kept = send(
        "desktop.action",
        "selfwork.objectives",
        {
            "action": "edit",
            "id": goal["id"],
            "text": "Raise the strategy's returns",
            "expected_revision": back["value"]["objectives"]["revision"],
        },
    )
    assert kept["value"]["objectives"]["items"][0]["text"] == "Raise the strategy's returns"


def test_phone_asks_rem_for_a_projects_objectives_by_project_id(linked):
    from gofer.ui import rem_objective_ideas

    _, _, context, send, home, _, _, _ = linked
    _, second = rem_projects(home, context)
    usual = home.generate
    broad = [
        "Improve UX across the project's features",
        "Audit unit tests and rewrite or remove unhelpful ones",
        "Harden error handling across the project",
    ]

    def generate(prompt: str, config: Any, cancel: Any) -> Any:
        if prompt.startswith(rem_objective_ideas.RULES):
            return {"objectives": [{"text": t} for t in broad]}
        return usual(prompt, config, cancel)

    home.generate = generate
    view = send("desktop.read", "selfwork.read")["value"]
    assert view["objectives"]["askable"] is True
    asked = send(
        "desktop.action", "selfwork.objectives", {"action": "ask", "project_id": second["id"]}
    )
    assert asked["ok"], asked
    assert second["root"] not in json.dumps(asked["value"])
    assert asked["value"]["objectives"]["asking"][second["id"]]["state"] in {"asking", "added"}
    home.self_work.ideas._threads[second["root"]].join(timeout=10)
    after = send("desktop.read", "selfwork.read")["value"]["objectives"]
    assert after["asking"][second["id"]]["state"] == "added"
    assert [i["text"] for i in after["projects"][second["id"]]["items"]] == broad
    # The global list can't be asked about.
    assert send("desktop.action", "selfwork.objectives", {"action": "ask"})["code"] == "invalid"
