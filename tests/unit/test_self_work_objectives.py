"""Self-work objectives: the checklist of session-wide goals the user and Rem share.

Covers the store's rules (the user may change anything; Rem writes only high-order goals and
checks one off only against written doneness criteria), Rem setting them from chat, self-work
writing its own when the list is empty at a session's start or when it runs out of work, and
every new self-work task reading the list fresh. Fake providers and isolated data only.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from gofer.ui import rem_objective_fit as objective_fit
from gofer.ui import rem_objectives as objectives
from gofer.ui import rem_self_work_windows as windows
from gofer.ui.headless import agent_request_refusal
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_home_models import Decision
from gofer.ui.rem_kanban import get_ticket, read_tickets
from tests.unit.test_rem_kanban import add, api_server, at, project_of, set_options, tick, wait_for
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_rem_pause_resume import reply_to
from tests.unit.test_self_work_auto_approve import Rankings, ideas, turn_on
from tests.unit.test_self_work_fill import quiet, worker_titles

assert api_server  # a fixture, imported for the HTTP test below

RESEARCH = "Research new features for project"
PLANNING = "You are Rem, choosing the objectives for your own self-work"
UX = "Raise UX quality of the studio"


def change(home: RemHome, payload: dict[str, Any], **who: Any) -> dict[str, Any]:
    with home.store.connect() as db:
        return objectives.change(db, home.store, payload, **who)


def listed(home: RemHome) -> list[dict[str, Any]]:
    return list(home.self_work.objectives()["items"])


def texts(home: RemHome) -> list[str]:
    return [item["text"] for item in listed(home)]


def task_prompt(home: RemHome, title: str) -> str:
    return str(next(t for t in home.store.tasks() if t["title"] == title)["prompt"])


def planner(home: RemHome, plan: dict[str, Any]) -> list[str]:
    """Rem's model: answers objective planning with ``plan``, other calls as usual."""
    prompts: list[str] = []
    usual = home.generate

    def generate(prompt: str, config: Any, cancel: Any) -> dict[str, Any]:
        if prompt.startswith(PLANNING):
            prompts.append(prompt)
            return plan
        return usual(prompt, config, cancel)

    home.generate = generate
    return prompts


def planned(home: RemHome) -> None:
    thread = home.self_work._planner
    if thread is not None:
        thread.join(timeout=5)


def checker(
    home: RemHome,
    serves: dict[str, str] | None = None,
    scopes: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Rem's model: judges tickets by title (title -> objective text, missing for none) and
    scopes objectives by text (text -> project roots). Returns the records it was shown."""
    shown: list[dict[str, Any]] = []
    usual = home.generate

    def generate(prompt: str, config: Any, cancel: Any) -> dict[str, Any]:
        if not prompt.startswith(objective_fit.CHECK_PREFIX):
            return usual(prompt, config, cancel)
        records = json.loads(prompt.split("Records:\n", 1)[1])
        shown.append(records)
        ids = {o["text"]: o["id"] for o in records["objectives"]}
        return {
            "tickets": [
                {
                    "ticket_id": t["ticket_id"],
                    "objective": ids.get((serves or {}).get(t["title"], ""), ""),
                    "reason": "Fits." if t["title"] in (serves or {}) else "Unrelated work.",
                }
                for t in records["tickets"]
            ],
            "scopes": [
                {"objective": ids[text], "projects": roots}
                for text, roots in (scopes or {}).items()
            ],
        }

    home.generate = generate
    return shown


def step(home: RemHome) -> None:
    """One pass, letting a check of tickets against the objectives finish first."""
    for _ in range(4):
        tick(home)
        thread = home.self_work._checker
        if thread is None or not thread.is_alive():
            return
        thread.join(timeout=5)


def calm(home: RemHome) -> bool:
    """Nothing running and no check of tickets against the objectives under way."""
    step(home)
    checking = home.self_work._checker is not None and home.self_work._checker.is_alive()
    return not checking and not home.self_work.view()["state"]["active_threads"]


def stored(home: RemHome, title: str) -> dict[str, Any]:
    with home.store.read() as db:
        return next(t for t in read_tickets(db) if t["title"] == title)


def report_rows(home: RemHome, kind: str) -> list[dict[str, Any]]:
    with home.store.read() as db:
        rows = [json.loads(r[0]) for r in db.execute("SELECT body FROM self_work_report")]
    return [r for r in rows if r["kind"] == kind]


# The store's rules ---------------------------------------------------------------------


def test_the_user_adds_edits_reorders_checks_and_removes_objectives(board):
    home, _, _ = board
    first = change(home, {"action": "add", "text": "Raise UX quality of the studio"}, actor="user")
    assert first["revision"] == 1
    change(home, {"action": "add", "text": "Speed", "done_when": "Opens in 1 s"}, actor="user")
    a, b = listed(home)
    assert b["done_when"] == "Opens in 1 s" and b["created_by"] == "user"
    change(home, {"action": "move", "id": b["id"], "index": 0}, actor="user")
    assert texts(home) == ["Speed", "Raise UX quality of the studio"]
    change(home, {"action": "edit", "id": a["id"], "text": "Raise UX quality"}, actor="user")
    # The user's own judgment is enough: no criteria or evidence needed.
    change(home, {"action": "check", "id": a["id"]}, actor="user")
    done = next(i for i in listed(home) if i["id"] == a["id"])
    assert done["done"] is True and done["done_by"] == "user"
    change(home, {"action": "uncheck", "id": a["id"]}, actor="user")
    assert not next(i for i in listed(home) if i["id"] == a["id"])["done"]
    change(home, {"action": "remove", "id": b["id"]}, actor="user")
    assert texts(home) == ["Raise UX quality"]
    with pytest.raises(objectives.ObjectiveRefused) as stale:
        change(home, {"action": "remove", "id": a["id"], "expected_revision": 1}, actor="user")
    assert stale.value.code == "conflict"
    with pytest.raises(objectives.ObjectiveRefused) as gone:
        change(home, {"action": "check", "id": b["id"]}, actor="user")
    assert gone.value.code == "not_found"


@pytest.mark.parametrize(
    "text",
    [
        "Fix bugs",  # too short to name an area and a direction
        "Finish REM-42 and REM-43",  # a ticket, not a goal
        "Speed up src/gofer/ui/rem_home.py",  # one file, not an area
    ],
)
def test_rem_writes_only_high_order_goals(board, text):
    home, _, _ = board
    for source in ("chat", "self_work"):
        with pytest.raises(objectives.ObjectiveRefused):
            change(home, {"action": "add", "text": text}, actor="rem", source=source)
    # The user may still write anything they like.
    change(home, {"action": "add", "text": text}, actor="user")
    assert texts(home) == [text]


def test_rem_keeps_only_a_few_open_objectives_of_its_own(board):
    home, _, _ = board
    for number in range(objectives.MAX_OPEN_BY_REM):
        change(
            home,
            {"action": "add", "text": f"Improve the reliability of area {number}"},
            actor="rem",
            source="self_work",
        )
    with pytest.raises(objectives.ObjectiveRefused, match="at most"):
        change(
            home,
            {"action": "add", "text": "Improve the reliability of one more area"},
            actor="rem",
            source="self_work",
        )


def test_rem_checks_off_only_against_written_criteria_with_evidence(board):
    home, _, _ = board
    change(home, {"action": "add", "text": "Raise UX quality of the studio"}, actor="user")
    change(
        home,
        {
            "action": "add",
            "text": "Make every starter workflow run on Windows",
            "done_when": "All seven starters pass on the Windows CI job",
        },
        actor="user",
    )
    open_ended, finite = listed(home)
    # Open-ended objectives stay open: there is no finish line to meet.
    with pytest.raises(objectives.ObjectiveRefused, match="open-ended"):
        change(
            home,
            {"action": "check", "checks": [{"id": open_ended["id"], "evidence": "Looks better"}]},
            actor="rem",
            source="self_work",
        )
    # Criteria without evidence aren't shown to be met.
    with pytest.raises(objectives.ObjectiveRefused):
        change(home, {"action": "check", "id": finite["id"]}, actor="rem", source="self_work")
    change(
        home,
        {
            "action": "check",
            "checks": [{"id": finite["id"], "evidence": "Windows job run 812 passed all seven."}],
        },
        actor="rem",
        source="self_work",
    )
    done = next(i for i in listed(home) if i["id"] == finite["id"])
    assert done["done"] and done["done_by"] == "rem"
    assert done["done_when"] == "All seven starters pass on the Windows CI job"
    assert done["done_evidence"] == "Windows job run 812 passed all seven."
    # On its own, self-work can't rewrite, reopen or drop what the user set.
    for action in ("edit", "remove", "uncheck", "move"):
        with pytest.raises(objectives.ObjectiveRefused) as refused:
            change(
                home,
                {"action": action, "id": finite["id"], "text": "Other", "index": 0},
                actor="rem",
                source="self_work",
            )
        assert refused.value.code == "unauthorized"


# Rem sets them from the user's instructions in chat -------------------------------------


def decide_objectives(home: RemHome, body: str, request: dict[str, Any]) -> str:
    home.generate = lambda *args: {"reply": "On it.", "tasks": [], "objectives": request}
    return reply_to(home, f"objectives-{abs(hash(body))}", body)


def test_chat_instructions_set_the_objectives_and_keep_done_ones(board):
    home, _, _ = board
    change(home, {"action": "add", "text": "Old focus that was finished"}, actor="user")
    change(home, {"action": "check", "id": listed(home)[0]["id"]}, actor="user")
    change(home, {"action": "add", "text": "Old focus still open"}, actor="user")
    reply = decide_objectives(
        home,
        "Self-work tonight: focus on the studio's UX and Second Brain reliability",
        {
            "action": "set",
            "items": [
                {"text": "Raise UX quality of the workflow studio"},
                {"text": "Improve reliability of the Second Brain tools"},
            ],
        },
    )
    assert texts(home) == [
        "Raise UX quality of the workflow studio",
        "Improve reliability of the Second Brain tools",
        "Old focus that was finished",
    ]
    assert "Set the self-work objectives; 2 are open" in reply
    # A task-sized "objective" is refused, and the reply says so instead of claiming it.
    reply = decide_objectives(
        home, "Add an objective: fix REM-12", {"action": "add", "items": [{"text": "Fix REM-12"}]}
    )
    assert "I didn't change the objectives" in reply
    assert "Fix REM-12" not in texts(home)


def test_the_user_saying_an_objective_is_done_lets_rem_check_it_off(board):
    home, _, _ = board
    change(home, {"action": "add", "text": "Raise UX quality of the studio"}, actor="user")
    identity = listed(home)[0]["id"]
    decide_objectives(
        home,
        "The studio UX objective is done, tick it",
        {
            "action": "check",
            "checks": [{"id": identity, "evidence": "The user said it's done."}],
            "done_when": "The user judged it done",
        },
    )
    item = listed(home)[0]
    assert item["done"] and item["done_by"] == "rem"
    assert item["done_when"] == "The user judged it done"


def test_rem_is_told_how_to_write_objectives():
    rules = objectives.HIGH_ORDER_RULE + objectives.DONE_RULE
    for phrase in ("high-order goal", "whole session", "one task could finish", "done_when"):
        assert phrase in rules
    # The chat decision accepts the field and refuses unknown actions.
    Decision.model_validate({"reply": "ok", "objectives": {"action": "set", "items": []}})
    with pytest.raises(ValueError):
        Decision.model_validate({"reply": "ok", "objectives": {"action": "wipe"}})


# Self-work writes, keeps and reads them ------------------------------------------------


def test_an_empty_list_at_session_start_gets_rems_own_high_order_objectives(board):
    home, fake, _ = board
    prompts = planner(
        home,
        {
            "objectives": [
                {"text": "Raise UX quality of the workflow studio"},
                {"text": "Fix typo"},  # task-sized: left out
                {
                    "text": "Improve reliability of the Second Brain tools",
                    "done_when": "No open Second Brain reliability tickets",
                },
            ]
        },
    )
    at(home, 15)
    tick(home)
    planned(home)
    assert len(prompts) == 1
    assert "the objectives list is empty" in prompts[0]
    assert "high-order goal" in prompts[0] and "one task could finish" in prompts[0]
    assert texts(home) == [
        "Raise UX quality of the workflow studio",
        "Improve reliability of the Second Brain tools",
    ]
    assert all(item["created_by"] == "rem" for item in listed(home))
    said = [m["body"] for m in home.store.conversation()["messages"]]
    assert any(body.startswith("Set my own self-work objectives") for body in said)
    assert any("Left out “Fix typo”" in body for body in said)
    # Then self-work works toward them.
    wait_for(lambda: not tick(home) and bool(worker_titles(fake)), 8)
    assert all(t.startswith("Find work for “") for t in worker_titles(fake))


def test_objectives_already_set_stay_in_force_at_session_start(board):
    home, _, _ = board
    change(home, {"action": "add", "text": "Raise UX quality of the studio"}, actor="user")
    prompts = planner(home, {"objectives": [{"text": "Something else entirely new"}]})
    at(home, 15)
    tick(home)
    planned(home)
    assert prompts == []
    assert texts(home) == ["Raise UX quality of the studio"]


def test_each_new_task_reads_the_objectives_fresh_and_names_the_one_it_serves(board):
    home, fake, _ = board
    fake.hold.clear()
    set_options(home, max_threads=1)
    change(home, {"action": "add", "text": "Raise UX quality of the studio"}, actor="user")
    checker(home, {"Polish the node picker": UX})
    first = add(home, "Polish the node picker")
    at(home, 15)
    step(home)
    assert "Raise UX quality of the studio" in task_prompt(home, "Polish the node picker")
    # The user changes the list while that task runs; the next task sees the new list.
    change(home, {"action": "add", "text": "Optimize performance of file handling"}, actor="user")
    perf = listed(home)[1]["id"]
    second = add(home, "Cache file listings")
    with home.store.connect() as db:
        ticket = get_ticket(db, second["id"])
        ticket["objective_id"] = perf
        from gofer.ui.rem_kanban import put_ticket

        put_ticket(db, ticket)
    fake.hold.set()
    wait_for(lambda: not tick(home) and "Cache file listings" in worker_titles(fake), 8)
    prompt = task_prompt(home, "Cache file listings")
    assert "Optimize performance of file handling" in prompt
    assert "Do this ticket's work only" in prompt
    runs = [r for t in home.store.tasks() for r in t["recent_runs"]]
    served = [r.get("objective") for r in runs if r["recipe"]["title"] == "Cache file listings"]
    assert served == [{"id": perf, "text": "Optimize performance of file handling"}]
    assert first["title"] == "Polish the node picker"


def test_research_proposals_record_the_objective_they_serve(board):
    home, fake, _ = board
    change(home, {"action": "add", "text": UX}, actor="user")
    change(home, {"action": "add", "text": "Optimize performance of file handling"}, actor="user")
    goal, perf = (item["id"] for item in listed(home))
    # Only the studio objective concerns this project, so research looks only for that one.
    checker(home, scopes={UX: [project_of(home)], "Optimize performance of file handling": []})
    title = f"Find work for “{UX}” in project"
    fake.answers[title] = (
        "Done.\n```json\n"
        '{"proposals":[{"title":"Searchable node picker","description":"d","why":"w"},'
        f'{{"title":"Faster file listing","description":"d","why":"w","objective":"{perf}"}}],'
        '"duplicates":[]}\n```'
    )
    set_options(home, max_threads=2, auto_pull_backlog=False)
    at(home, 15)
    step(home)
    wait_for(lambda: not tick(home) and title in worker_titles(fake), 8)
    assert [t for t in worker_titles(fake) if "Find work" in t] == [title]
    prompt = task_prompt(home, title)
    assert f"This research is for one self-work objective: “{UX}” [{goal}]" in prompt
    assert "never propose unrelated work" in prompt
    runs = [r for t in home.store.tasks() for r in t["recent_runs"]]
    assert [r.get("objective") for r in runs if r["recipe"]["title"] == title] == [
        {"id": goal, "text": UX}
    ]
    # The next round of research for this objective starts from this run's report.
    assert [r["self_work"]["angle"] for r in runs if r["recipe"]["title"] == title] == [
        f"objective:{goal}"
    ]
    wait_for(lambda: not tick(home) and len(home.kanban.board()["tickets"]) >= 2, 8)
    # A proposal serves the objective its research was for, unless it names another.
    assert stored(home, "Searchable node picker").get("objective_id") == goal
    assert stored(home, "Faster file listing").get("objective_id") == perf


def test_running_out_of_work_lets_rem_add_objectives_and_research_again(board):
    home, fake, _ = board
    change(home, {"action": "add", "text": "Raise UX quality of the studio"}, actor="user")
    set_options(home, max_threads=4)
    fake.answers[f"Find work for “{UX}” in project"] = (
        'Done.\n```json\n{"proposals":[],"duplicates":[]}\n```'
    )
    prompts = planner(home, {"objectives": [{"text": "Harden the command line against bad input"}]})
    at(home, 15)
    tick(home)
    wait_for(lambda: quiet(home) and home.self_work._planner is not None, 8)
    planned(home)
    assert len(prompts) == 1 and "run out of work" in prompts[0]
    assert "Harden the command line against bad input" in texts(home)
    with home.store.read() as db:
        session = home.store.setting(db, "self_work_state")["session"]
    assert session["round"] == 1, "new objectives start a new research round at once"
    # The research after it looks for work on the new objective.
    wait_for(
        lambda: (
            not tick(home)
            and "Find work for “Harden the command line against bad input” in project"
            in worker_titles(fake)
        ),
        8,
    )


# The objectives are a hard gate -----------------------------------------------------


def test_with_open_objectives_only_tickets_that_advance_one_start(board):
    home, fake, _ = board
    set_options(home, max_threads=4, auto_pull_backlog=True)
    change(home, {"action": "add", "text": "Improve Raticode UI responsiveness"}, actor="user")
    goal = listed(home)[0]["id"]
    shown = checker(
        home,
        {
            "Render the side pane lazily": "Improve Raticode UI responsiveness",
            "Debounce board polling": "Improve Raticode UI responsiveness",
        },
    )
    fake.answers = Rankings(home, lambda t: {"impact": 4, "relevance": 4})
    add(home, "Render the side pane lazily")
    add(home, "Export the board to CSV")
    add(home, "Debounce board polling", status="backlog")
    add(home, "Rewrite the README", status="backlog")
    at(home, 15)
    step(home)
    # One check judged every waiting ticket against the objectives, with them listed.
    assert len(shown) == 1 and {t["title"] for t in shown[0]["tickets"]} == {
        "Render the side pane lazily",
        "Export the board to CSV",
        "Debounce board polling",
        "Rewrite the README",
    }
    assert shown[0]["objectives"][0]["text"] == "Improve Raticode UI responsiveness"
    wait_for(lambda: not tick(home) and "Debounce board polling" in worker_titles(fake), 8)
    titles = worker_titles(fake)
    assert "Render the side pane lazily" in titles
    assert "Export the board to CSV" not in titles and "Rewrite the README" not in titles
    # The backlog ranking only saw the ticket that advances an objective.
    rank = next(t for t in home.store.tasks() if t["title"].startswith("Rank the backlog"))
    assert "Debounce board polling" in rank["prompt"] and "Rewrite the README" not in rank["prompt"]
    assert "these were picked because they do" in rank["prompt"]
    wait_for(lambda: calm(home), 8)
    # Unrelated tickets stay where they were, and the board says why.
    board_view = {t["title"]: t for t in home.kanban.board()["tickets"]}
    assert board_view["Export the board to CSV"]["status"] == "todo"
    assert board_view["Rewrite the README"]["status"] == "backlog"
    assert board_view["Export the board to CSV"]["objective"] is None
    assert board_view["Export the board to CSV"]["objective_left"].startswith(
        objective_fit.UNRELATED
    )
    assert "Unrelated work." in board_view["Rewrite the README"]["objective_left"]
    assert board_view["Render the side pane lazily"]["objective"] == {
        "id": goal,
        "text": "Improve Raticode UI responsiveness",
    }
    assert board_view["Render the side pane lazily"]["objective_left"] == ""
    # Each started run names the objective it serves.
    runs = [r for t in home.store.tasks() for r in t["recent_runs"]]
    for title in ("Render the side pane lazily", "Debounce board polling"):
        assert [r.get("objective", {}).get("id") for r in runs if r["recipe"]["title"] == title]
        assert all(r["objective"]["id"] == goal for r in runs if r["recipe"]["title"] == title)
    # The report says once which tickets were left alone, however many passes ran.
    left = report_rows(home, "off_objective")
    assert sorted(r["title"] for r in left) == ["Export the board to CSV", "Rewrite the README"]
    notes = [r["text"] for r in report_rows(home, "note")]
    assert any(
        "advance an open self-work objective" in n and n.startswith("Left REM-") for n in notes
    )


def test_when_nothing_relates_self_work_proposes_tickets_for_the_objectives(board):
    home, fake, _ = board
    set_options(home, max_threads=2, auto_pull_backlog=True)
    turn_on(home)
    change(home, {"action": "add", "text": "Improve Raticode UI responsiveness"}, actor="user")
    goal = listed(home)[0]["id"]
    checker(home)  # nothing on the board advances it
    add(home, "Export the board to CSV")
    add(home, "Rewrite the README", status="backlog")
    title = "Find work for “Improve Raticode UI responsiveness” in project"
    fake.answers = Rankings(home, lambda t: {"impact": 4, "relevance": 4})
    fake.answers[title] = ideas("Virtualize the chat list")
    at(home, 15)
    step(home)
    wait_for(lambda: not tick(home) and title in worker_titles(fake), 8)
    # No unrelated ticket is pulled; research looks for work on the objective instead.
    assert worker_titles(fake) == [title]
    assert not any(t.startswith("Research ") for t in worker_titles(fake))
    # Auto-approval is on, so its ticket is approved, ranked and worked like any other.
    wait_for(lambda: not tick(home) and "Virtualize the chat list" in worker_titles(fake), 8)
    assert stored(home, "Virtualize the chat list")["objective_id"] == goal
    wait_for(lambda: calm(home), 8)
    titles = worker_titles(fake)
    assert "Export the board to CSV" not in titles and "Rewrite the README" not in titles
    assert stored(home, "Export the board to CSV")["status"] == "todo"
    assert stored(home, "Rewrite the README")["status"] == "backlog"


def test_without_auto_approval_objective_proposals_wait_for_the_user(board):
    home, fake, _ = board
    set_options(home, max_threads=2)
    change(home, {"action": "add", "text": "Improve Raticode UI responsiveness"}, actor="user")
    checker(home)
    add(home, "Export the board to CSV")
    title = "Find work for “Improve Raticode UI responsiveness” in project"
    fake.answers[title] = ideas("Virtualize the chat list")
    at(home, 15)
    step(home)
    wait_for(lambda: calm(home), 8)
    proposal = stored(home, "Virtualize the chat list")
    assert proposal["proposal"]["state"] == "pending" and proposal["status"] == "backlog"
    assert "Virtualize the chat list" not in worker_titles(fake)
    assert "Export the board to CSV" not in worker_titles(fake)


def test_an_objective_no_project_fits_gets_no_research(board):
    home, fake, _ = board
    change(home, {"action": "add", "text": "Grow the newsletter audience"}, actor="user")
    checker(home, scopes={"Grow the newsletter audience": []})
    at(home, 15)
    step(home)
    wait_for(lambda: calm(home), 8)
    assert worker_titles(fake) == []
    notes = [r["text"] for r in report_rows(home, "note")]
    assert any("None of Rem's projects fits the open objectives" in n for n in notes)


def test_a_failed_check_starts_nothing_until_it_is_checked_again(board):
    home, fake, _ = board
    change(home, {"action": "add", "text": "Improve Raticode UI responsiveness"}, actor="user")
    calls: list[str] = []

    def down(prompt: str, config: Any, cancel: Any) -> dict[str, Any]:
        if prompt.startswith(objective_fit.CHECK_PREFIX):
            calls.append(prompt)
            raise RuntimeError("provider down")
        return {"reply": "Noted.", "tasks": [], "memories": []}

    home.generate = down
    add(home, "Render the side pane lazily")
    at(home, 15)
    step(home)
    step(home)
    assert len(calls) == 1, "a failed check isn't retried on every pass"
    assert "Render the side pane lazily" not in worker_titles(fake)
    assert stored(home, "Render the side pane lazily")["status"] == "todo"
    # Ten minutes later it is offered again, and starts once it is judged.
    checker(home, {"Render the side pane lazily": "Improve Raticode UI responsiveness"})
    at(home, 26)
    step(home)
    wait_for(lambda: not tick(home) and "Render the side pane lazily" in worker_titles(fake), 8)


def test_changing_the_objectives_judges_the_tickets_again(board):
    home, fake, _ = board
    fake.hold.clear()
    # Research for the first objective takes one thread; the ticket gets the other.
    set_options(home, max_threads=2)
    change(home, {"action": "add", "text": "Improve Raticode UI responsiveness"}, actor="user")
    shown = checker(home, {"Export the board to CSV": "Make board data easy to take elsewhere"})
    add(home, "Export the board to CSV")
    at(home, 15)
    step(home)
    assert stored(home, "Export the board to CSV")["status"] == "todo"
    # The user adds an objective the ticket advances: it is judged again, and starts.
    change(home, {"action": "add", "text": "Make board data easy to take elsewhere"}, actor="user")
    step(home)
    assert len(shown) == 2
    assert stored(home, "Export the board to CSV")["status"] == "in_progress"
    fake.hold.set()


def test_an_unrelated_ticket_still_starts_when_the_user_moves_it(board):
    home, fake, _ = board
    change(home, {"action": "add", "text": "Improve Raticode UI responsiveness"}, actor="user")
    checker(home)
    item = add(home, "Export the board to CSV")
    at(home, 15)
    step(home)
    assert stored(home, "Export the board to CSV")["status"] == "todo"
    home.self_work.start_now(item["id"], "user")
    assert stored(home, "Export the board to CSV")["status"] == "in_progress"


def test_with_no_open_objectives_nothing_is_gated(board):
    home, fake, _ = board
    change(home, {"action": "add", "text": "Improve Raticode UI responsiveness"}, actor="user")
    change(home, {"action": "check", "id": listed(home)[0]["id"]}, actor="user")
    shown = checker(home)
    add(home, "Export the board to CSV")
    at(home, 15)
    step(home)
    assert shown == [], "no check runs without an open objective"
    # The ticket starts, and research keeps its usual angles.
    wait_for(
        lambda: (
            not tick(home) and {"Export the board to CSV", RESEARCH} <= set(worker_titles(fake))
        ),
        8,
    )
    assert home.kanban.board()["tickets"][0]["objective_left"] == ""


def test_the_period_report_names_objectives_and_tickets_left_alone(board):
    home, fake, _ = board
    change(home, {"action": "add", "text": "Improve Raticode UI responsiveness"}, actor="user")
    checker(home, {"Render the side pane lazily": "Improve Raticode UI responsiveness"})
    add(home, "Render the side pane lazily")
    add(home, "Export the board to CSV")
    with home.store.connect() as db:
        schedule = windows.read(db)
        schedule["period"] = {"id": "p1", "started_at": "2000-01-01T00:00:00+00:00", "from_seq": 0}
        windows.write(db, schedule)
    at(home, 15)
    step(home)
    wait_for(lambda: calm(home), 8)
    with home.store.connect() as db:
        # Report rows carry the real time, so the period ends after now.
        closed = windows.close_period(db, datetime.now(UTC) + timedelta(days=1), "user")
    assert closed is not None
    report, message = closed
    assert report["counts"]["off_objective"] == 1
    body = message["body"]
    assert "left 1 ticket off the objectives alone" in body
    assert "“Render the side pane lazily” (REM-1), for “Improve Raticode UI responsiveness”" in body
    assert "- Left where they are, not advancing an open objective: REM-2" in body


def test_a_ticket_judged_for_older_objectives_or_text_is_judged_again():
    goal: dict[str, Any] = {"id": "a1", "text": "Improve speed", "done": False}
    open_list: dict[str, Any] = {"revision": 1, "items": [goal]}
    ticket = {"id": "t", "title": "Cache", "description": "", "project_dir": "/p", "status": "todo"}
    assert objective_fit.judge(ticket, {"revision": 0, "items": []})[0] == "free"
    assert objective_fit.judge(ticket, open_list)[0] == "unknown"
    objective_fit.record(ticket, open_list, "", "No", "now")
    assert objective_fit.judge(ticket, open_list)[0] == "unrelated"
    ticket["title"] = "Cache file listings"
    assert objective_fit.judge(ticket, open_list)[0] == "unknown"
    objective_fit.record(ticket, open_list, "a1", "Yes", "now")
    assert objective_fit.judge(ticket, open_list)[:2] == (
        "related",
        {"id": "a1", "text": "Improve speed"},
    )
    assert ticket["objective_id"] == "a1"
    # Checked off, it no longer counts, and the ticket needs a new judgment.
    done = {"revision": 2, "items": [{**goal, "done": True}]}
    assert objective_fit.judge(ticket, done)[0] == "free"
    other = {"revision": 3, "items": [{"id": "b2", "text": "Improve docs", "done": False}]}
    assert objective_fit.judge(ticket, other)[0] == "unknown"


# The user's HTTP route ----------------------------------------------------------------


def test_the_side_pane_reads_objectives_with_the_tasks_and_only_the_user_writes(api_server):
    request, _ = api_server
    added = request(
        "POST", "/api/rem/objectives", body={"action": "add", "text": "Raise UX quality"}
    )
    assert added.status == 200, added.json()
    assert added.json()["items"][0]["text"] == "Raise UX quality"
    tasks = request("GET", "/api/rem/tasks").json()
    assert tasks["objectives"]["items"][0]["text"] == "Raise UX quality"
    stale = request(
        "POST",
        "/api/rem/objectives",
        body={"action": "add", "text": "Another", "expected_revision": 0},
    )
    assert stale.status == 409 and stale.json()["code"] == "conflict"
    assert request("GET", "/api/rem/objectives").json()["open"] == 1
    # Agent tokens, such as a worker's, can read but never change them.
    assert agent_request_refusal("POST", "/api/rem/objectives")
    assert agent_request_refusal("GET", "/api/rem/objectives") is None
