"""Self-work fills its threads after 15 quiet minutes: to-dos, ranked backlog, then research."""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.rem_home import RemHome
from gofer.ui.rem_kanban import get_ticket
from gofer.ui.rem_self_work import TRIAGE_FOR, TRIAGE_TICKETS
from tests.unit.test_rem_kanban import (
    BASE,
    PROPOSAL,
    add,
    add_proposal,
    at,
    ranked,
    set_options,
    settle,
    start_new_session,
    tick,
    ticket,
    ticket_or_none,
    wait_for,
)
from tests.unit.test_rem_kanban import board as board

RANK = "Rank the backlog for project"
RESEARCH = "Research new features for project"


def worker_titles(fake: Any) -> list[str]:
    return [
        c["workflow"]["chatThreadTitle"].removeprefix("Rem task: ")
        for c in fake.calls
        if "independent read-only reviewer" not in c["agent_instructions"]
    ]


def activity(home: RemHome) -> list[str]:
    return [item["title"] for item in home.self_work.view()["state"]["activity"]]


def more_projects(home: RemHome, project: Path, *names: str) -> list[str]:
    roots = [str(project)]
    listed = [{"root": str(project), "name": project.name}]
    for name in names:
        folder = project.parent / name
        folder.mkdir()
        roots.append(str(folder))
        listed.append({"root": str(folder), "name": name})
    home.configure({"provider": "codex", "model": "cli-default", "projects": listed})
    home.kanban.set_participant_projects(
        "user", {"participant_id": "rem", "projects": roots, "expected_revision": 1}
    )
    return roots


def quiet(home: RemHome) -> bool:
    return not tick(home) and not home.self_work.view()["state"]["active_threads"]


def add_backlog(home: RemHome, title: str, **fields: Any) -> dict[str, Any]:
    ticket: dict[str, Any] = add(home, title, status="backlog", **fields)
    return ticket


def ranking(*items: tuple[dict[str, Any], int, int, bool, str]) -> str:
    return (
        "Done.\n```json\n"
        + json.dumps(
            {
                "tickets": [
                    {
                        "ticket_id": created["id"],
                        "impact": impact,
                        "relevance": relevance,
                        "skip": skip,
                        "reason": reason,
                    }
                    for created, impact, relevance, skip, reason in items
                ]
            }
        )
        + "\n```"
    )


# Filling the threads ---------------------------------------------------------------


@pytest.mark.parametrize("auto_pull", [True, False])
def test_an_empty_board_fills_every_thread_with_research_at_fifteen_minutes(board, auto_pull: bool):
    """Regression: with auto-pull on and no tickets, Rem said it was out of work and stopped."""
    home, fake, project = board
    fake.hold.clear()
    more_projects(home, project, "alpha", "beta")
    set_options(home, max_threads=3, auto_pull_backlog=auto_pull)
    at(home, 14.9)
    tick(home)
    assert home.self_work.view()["state"]["active_threads"] == 0
    at(home, 15)
    tick(home)
    state = home.self_work.view()["state"]
    assert state["mode"] == "working" and state["active_threads"] == 3
    assert sorted(item["project"] for item in state["activity"]) == ["alpha", "beta", "project"]
    fake.hold.set()
    wait_for(lambda: quiet(home) and home.self_work.view()["state"]["detail"].startswith("Out"), 8)


def test_threads_stay_full_in_one_project_with_different_research(board):
    home, fake, _ = board
    fake.hold.clear()
    set_options(home, max_threads=3)
    at(home, 15)
    tick(home)
    assert len(activity(home)) == 3 and len(set(activity(home))) == 3
    fake.hold.set()
    wait_for(lambda: quiet(home), 8)
    assert len(set(worker_titles(fake))) == 3
    for _ in range(3):
        tick(home)
    assert len(worker_titles(fake)) == 3


def test_todos_come_first_and_research_uses_what_is_left(board):
    home, fake, _ = board
    fake.hold.clear()
    set_options(home, max_threads=3)
    add(home, "Only todo", priority="low")
    at(home, 15)
    tick(home)
    now = activity(home)
    assert "Only todo" in now and len(now) == 3
    assert sum("Research" in title for title in now) == 2
    fake.hold.set()
    settle(home, "Only todo")


def test_todos_alone_can_fill_every_thread(board):
    home, fake, _ = board
    fake.hold.clear()
    set_options(home, max_threads=2)
    for title in ("One", "Two", "Three"):
        add(home, title)
    at(home, 15)
    tick(home)
    assert not any("Research" in t for t in activity(home)) and len(activity(home)) == 2
    fake.hold.set()
    settle(home, "One", "Two", "Three")


def test_a_finished_ticket_is_replaced_by_the_next_job(board):
    home, fake, _ = board
    fake.hold.clear()
    set_options(home, max_threads=2)
    add(home, "First")
    add(home, "Second")
    at(home, 15)
    tick(home)
    assert home._finish(ticket(home, "First")["run_id"], "succeeded", "Finished it.")
    tick(home)
    assert len(activity(home)) == 2 and any("Research" in t for t in activity(home))
    fake.hold.set()
    settle(home, "Second")


def test_the_start_of_self_work_is_recorded_where_the_user_can_see_it(board):
    home, fake, _ = board
    fake.hold.clear()
    add(home, "Visible")
    at(home, 15)
    tick(home)
    view = home.self_work.view()
    notes = [note["text"] for note in view["report"]["notes"]]
    assert any(n.startswith("Started after 15 quiet minutes: 1 to-do") for n in notes)
    assert view["report"]["unseen"] is True
    assert "Visible" in activity(home)
    fake.hold.set()
    settle(home, "Visible")


def test_a_quiet_session_with_nothing_found_still_says_so(board):
    home, fake, _ = board
    at(home, 15)
    tick(home)
    wait_for(lambda: quiet(home) and home.self_work.view()["state"]["detail"].startswith("Out"), 8)
    assert "Started after 15 quiet minutes" in " ".join(
        n["text"] for n in home.self_work.view()["report"]["notes"]
    )


def test_a_prompt_stops_new_work_from_starting(board):
    home, fake, _ = board
    fake.hold.clear()
    set_options(home, max_threads=1)
    add(home, "Running")
    add(home, "Waits")
    at(home, 15)
    tick(home)
    with home.store.connect() as db:
        home.self_work.note_prompt(db)
    tick(home)
    fake.hold.set()
    settle(home, "Running")
    assert ticket(home, "Waits")["status"] == "todo"


# Proposals --------------------------------------------------------------------------


def test_new_ideas_wait_in_backlog_until_approved_then_go_to_todo(board):
    home, fake, _ = board
    fake.answers[RESEARCH] = PROPOSAL
    set_options(home, auto_pull_backlog=True)
    at(home, 15)
    tick(home)
    wait_for(lambda: not tick(home) and ticket_or_none(home, "Voice shortcuts") is not None, 8)
    wait_for(lambda: quiet(home), 8)
    idea = ticket(home, "Voice shortcuts")
    assert idea["status"] == "backlog" and idea["proposed"] is True
    assert "Voice shortcuts" not in worker_titles(fake)
    home.kanban.decide(
        "user", {"ticket_id": idea["ticket_id"], "expected_revision": idea["revision"]}, "approve"
    )
    approved = ticket(home, "Voice shortcuts")
    assert approved["status"] == "todo" and approved["proposed"] is False
    start_new_session(home, 30)
    settle(home, "Voice shortcuts")
    assert "Voice shortcuts" in worker_titles(fake)


def test_an_unapproved_idea_cannot_be_pulled_or_ranked(board):
    home, fake, _ = board
    fake.hold.clear()
    set_options(home, max_threads=1, auto_pull_backlog=True)
    add_proposal(home, "Pending idea")
    at(home, 15)
    tick(home)
    assert RANK not in activity(home) and not ticket(home, "Pending idea")["running"]
    fake.hold.set()


# Ranking the backlog ----------------------------------------------------------------


def test_backlog_is_ranked_first_then_pulled_best_first(board):
    home, fake, _ = board
    fake.hold.clear()
    set_options(home, max_threads=1, auto_pull_backlog=True)
    low = add_backlog(home, "Minor polish", priority="high")
    high = add_backlog(home, "Fix data loss", priority="low")
    mid = add_backlog(home, "Better search")
    fake.answers[RANK] = ranking(
        (low, 1, 2, False, "Cosmetic"),
        (high, 5, 5, False, "Loses work"),
        (mid, 3, 3, False, "Nice"),
    )
    at(home, 15)
    tick(home)
    assert activity(home) == [RANK]
    assert all(ticket(home, t)["status"] == "backlog" for t in ("Fix data loss", "Minor polish"))
    fake.hold.set()
    settle(home, "Fix data loss", "Better search", "Minor polish")
    pulled = [
        t for t in worker_titles(fake) if t in {"Fix data loss", "Better search", "Minor polish"}
    ]
    assert pulled == ["Fix data loss", "Better search", "Minor polish"]
    # Scores add up to what the pull used: 5 + 5, and one point off for low priority.
    assert ticket(home, "Fix data loss")["backlog_score"] == 9


def test_a_ticket_that_no_longer_makes_sense_is_left_out_and_explained(board):
    home, fake, _ = board
    set_options(home, max_threads=1, auto_pull_backlog=True)
    stale = add_backlog(home, "Port to the old API")
    good = add_backlog(home, "Useful work")
    fake.answers[RANK] = ranking(
        (stale, 1, 1, True, "The old API was removed"), (good, 4, 4, False, "Helps")
    )
    at(home, 15)
    tick(home)
    settle(home, "Useful work")
    wait_for(lambda: quiet(home), 8)
    left = ticket(home, "Port to the old API")
    assert left["status"] == "backlog" and left["skip_reason"] == "The old API was removed"
    assert "Port to the old API" not in worker_titles(fake)
    notes = " ".join(n["text"] for n in home.self_work.view()["report"]["notes"])
    assert "The old API was removed" in notes
    with home.store.read() as db:
        assert get_ticket(db, stale["id"])["status"] == "backlog"


def test_the_user_can_still_run_a_ticket_rem_left_out(board):
    home, _, _ = board
    created = add_backlog(home, "Actually wanted")
    ranked(home, "Actually wanted", skip=True, reason="Seems stale")
    home.kanban.move(
        "user", {"ticket_id": created["id"], "expected_revision": 1, "column": "in_progress"}
    )
    assert ticket(home, "Actually wanted")["running"]
    settle(home, "Actually wanted")
    assert ticket(home, "Actually wanted")["status"] == "done"


def test_editing_a_ticket_or_time_passing_makes_rem_rank_it_again(board):
    home, fake, _ = board
    fake.hold.clear()
    set_options(home, max_threads=1, auto_pull_backlog=True)
    created = add_backlog(home, "Edited later")
    ranked(home, "Edited later", skip=True, reason="Old")
    home.kanban.edit(
        "user", {"ticket_id": created["id"], "expected_revision": 1, "title": "Edited now"}
    )
    at(home, 15)
    tick(home)
    assert activity(home) == [RANK]
    fake.hold.set()


def test_a_ticket_edited_while_being_ranked_keeps_no_judgment_of_the_old_text(board):
    home, fake, _ = board
    fake.hold.clear()
    set_options(home, max_threads=1, auto_pull_backlog=True)
    created = add_backlog(home, "Port to the old API")
    fake.answers[RANK] = ranking((created, 1, 1, True, "The old API was removed"))
    at(home, 15)
    tick(home)
    assert activity(home) == [RANK]
    home.kanban.edit(
        "user", {"ticket_id": created["id"], "expected_revision": 1, "title": "Port to the new API"}
    )
    # The run about the old text finishes, and what it said about that text is dropped.
    with home.store.read() as db:
        record = home.store.setting(db, "self_work_state")["passes"][0]
    old_answer = ranking((created, 1, 1, True, "The old API was removed"))
    assert home._finish(record["run_id"], "succeeded", "Done")
    with home.store.connect() as db:
        run = home.store.get(db, "runs", record["run_id"])
        run["worker_result"] = old_answer
        home.store.put(db, "runs", run)
    home.self_work.reconcile()
    assert ticket(home, "Port to the new API")["skip_reason"] == ""
    # The new text gets its own ranking and can then run.
    fake.answers[RANK] = ranking((created, 4, 4, False, "Still useful"))
    fake.hold.set()
    settle(home, "Port to the new API")
    assert ticket(home, "Port to the new API")["skip_reason"] == ""
    assert "Port to the new API" in worker_titles(fake)


def test_editing_a_left_out_ticket_clears_the_reason_at_once(board):
    home, _, _ = board
    created = add_backlog(home, "Stale plan")
    ranked(home, "Stale plan", skip=True, reason="Seems stale")
    assert ticket(home, "Stale plan")["skip_reason"] == "Seems stale"
    home.kanban.edit(
        "user", {"ticket_id": created["id"], "expected_revision": 1, "description": "Now current"}
    )
    assert ticket(home, "Stale plan")["skip_reason"] == ""


def test_an_old_ranking_is_redone(board):
    home, fake, _ = board
    fake.hold.clear()
    set_options(home, max_threads=1, auto_pull_backlog=True)
    add_backlog(home, "Ranked long ago")
    expired = (BASE - TRIAGE_FOR - timedelta(minutes=1)).isoformat()
    ranked(home, "Ranked long ago", at=expired)
    at(home, 15)
    tick(home)
    assert activity(home) == [RANK]
    fake.hold.set()


def test_a_fresh_ranking_is_not_run_again(board):
    home, fake, _ = board
    fake.hold.clear()
    set_options(home, max_threads=1, auto_pull_backlog=True)
    add_backlog(home, "Ranked already")
    ranked(home, "Ranked already")
    at(home, 15)
    tick(home)
    assert activity(home) == ["Ranked already"]
    fake.hold.set()
    settle(home, "Ranked already")


def past_ranking(minutes_after: float = 15) -> float:
    return minutes_after + TRIAGE_FOR.total_seconds() / 60 + 1


def ranker_prompts(fake: Any) -> list[str]:
    return [
        " ".join(str(m["body"]) for m in c["messages"])
        for c in fake.calls
        if c["workflow"]["chatThreadTitle"] == f"Rem task: {RANK}"
        and "independent read-only reviewer" not in c["agent_instructions"]
    ]


def quiet_with_no_ranking(home: RemHome) -> bool:
    return not tick(home) and RANK not in activity(home) and not activity(home)


def test_a_left_out_ticket_stays_out_when_its_ranking_expires_in_the_same_session(board):
    """Regression: past 12 hours the expired judgment stopped counting and the ticket ran."""
    home, fake, _ = board
    set_options(home, max_threads=1, auto_pull_backlog=True)
    stale = add_backlog(home, "Port to the old API")
    fake.answers[RANK] = ranking((stale, 1, 1, True, "The old API was removed"))
    at(home, 15)
    tick(home)
    wait_for(lambda: quiet_with_no_ranking(home), 8)
    assert len(ranker_prompts(fake)) == 1
    # Still no prompt: the same session runs on past the ranking's life, and is ranked again.
    at(home, past_ranking())
    tick(home)
    wait_for(lambda: quiet_with_no_ranking(home) and len(ranker_prompts(fake)) == 2, 8)
    left = ticket(home, "Port to the old API")
    assert left["status"] == "backlog" and left["skip_reason"] == "The old API was removed"
    assert "Port to the old API" not in worker_titles(fake)


def test_a_left_out_ticket_stays_out_if_the_next_ranking_fails(board):
    home, fake, _ = board
    set_options(home, max_threads=1, auto_pull_backlog=True)
    stale = add_backlog(home, "Port to the old API")
    fake.answers[RANK] = ranking((stale, 1, 1, True, "The old API was removed"))
    at(home, 15)
    tick(home)
    wait_for(lambda: quiet_with_no_ranking(home), 8)
    fake.answers[RANK] = "No JSON at all."
    at(home, past_ranking())
    wait_for(lambda: quiet_with_no_ranking(home) and len(ranker_prompts(fake)) == 2, 8)
    for _ in range(3):
        tick(home)
    assert ticket(home, "Port to the old API")["status"] == "backlog"
    assert "Port to the old API" not in worker_titles(fake)


def test_a_new_ranking_can_bring_a_left_out_ticket_back(board):
    home, fake, _ = board
    set_options(home, max_threads=1, auto_pull_backlog=True)
    stale = add_backlog(home, "Maybe later")
    fake.answers[RANK] = ranking((stale, 1, 1, True, "Not yet"))
    at(home, 15)
    tick(home)
    wait_for(lambda: quiet_with_no_ranking(home), 8)
    fake.answers[RANK] = ranking((stale, 4, 4, False, "Needed now"))
    at(home, past_ranking())
    tick(home)
    settle(home, "Maybe later")
    assert ticket(home, "Maybe later")["status"] == "done"


def test_a_failed_ranking_pulls_nothing_and_is_tried_again_later(board):
    home, fake, _ = board
    set_options(home, max_threads=1, auto_pull_backlog=True)
    fake.answers[RANK] = "No JSON at all."
    created = add_backlog(home, "Unjudged", priority="critical")
    at(home, 15)
    tick(home)
    wait_for(lambda: quiet_with_no_ranking(home), 8)
    for _ in range(4):
        tick(home)
    assert ticket(home, "Unjudged")["status"] == "backlog"
    assert "Unjudged" not in worker_titles(fake) and len(ranker_prompts(fake)) == 1
    notes = " ".join(n["text"] for n in home.self_work.view()["report"]["notes"])
    assert "stay in Backlog" in notes
    fake.answers[RANK] = ranking((created, 3, 3, False, "Worth doing"))
    at(home, past_ranking())
    tick(home)
    settle(home, "Unjudged")
    assert len(ranker_prompts(fake)) == 2


def test_tickets_past_what_one_ranking_can_read_get_their_own(board):
    home, fake, _ = board
    set_options(home, max_threads=1, auto_pull_backlog=True)
    created = [add_backlog(home, f"Plan number {i:02d}") for i in range(TRIAGE_TICKETS + 1)]
    # The first run isn't shown this ticket, so only the second can judge it.
    fake.answers[RANK] = ranking((created[-1], 1, 1, True, "Not needed"))
    at(home, 15)
    tick(home)
    wait_for(lambda: quiet_with_no_ranking(home) and len(ranker_prompts(fake)) == 2, 8)
    first, second = ranker_prompts(fake)
    last = created[-1]["id"]
    assert last not in first and last in second and created[0]["id"] not in second
    assert ticket(home, f"Plan number {TRIAGE_TICKETS:02d}")["skip_reason"] == "Not needed"


def test_a_ticket_added_mid_session_is_ranked_before_it_can_be_pulled(board):
    home, fake, _ = board
    set_options(home, max_threads=1, auto_pull_backlog=True)
    first = add_backlog(home, "Early")
    fake.answers[RANK] = ranking((first, 3, 3, False, "Fine"))
    at(home, 15)
    tick(home)
    settle(home, "Early")
    wait_for(lambda: quiet_with_no_ranking(home), 8)
    fake.hold.clear()
    late = add_backlog(home, "Late")
    fake.answers[RANK] = ranking((late, 1, 1, True, "Not needed"))
    tick(home)
    assert activity(home) == [RANK]
    fake.hold.set()
    wait_for(lambda: quiet_with_no_ranking(home) and len(ranker_prompts(fake)) == 2, 8)
    assert ticket(home, "Late")["status"] == "backlog"
    assert "Late" not in worker_titles(fake)


def test_a_ticket_the_ranking_left_unjudged_is_reported_and_not_pulled(board):
    home, fake, _ = board
    set_options(home, max_threads=1, auto_pull_backlog=True)
    judged = add_backlog(home, "Judged")
    add_backlog(home, "Forgotten")
    fake.answers[RANK] = ranking((judged, 3, 3, False, "Fine"))
    at(home, 15)
    tick(home)
    settle(home, "Judged")
    wait_for(lambda: quiet_with_no_ranking(home), 8)
    assert ticket(home, "Forgotten")["status"] == "backlog"
    assert "Forgotten" not in worker_titles(fake)
    summaries = [w["summary"] for w in home.self_work.view()["report"]["work_done"]]
    assert any("1 not ranked, so not pulled." in text for text in summaries)


def test_with_auto_pull_off_the_backlog_is_left_alone(board):
    home, fake, _ = board
    add_backlog(home, "Not pulled")
    at(home, 15)
    tick(home)
    wait_for(lambda: quiet(home), 8)
    assert ticket(home, "Not pulled")["status"] == "backlog"
    assert RANK not in worker_titles(fake) and "Not pulled" not in worker_titles(fake)


def test_ranking_needs_no_saved_report(board):
    home, fake, _ = board
    set_options(home, max_threads=1, auto_pull_backlog=True)
    created = add_backlog(home, "Ranked work")
    fake.answers[RANK] = ranking((created, 3, 3, False, "Fine"))
    at(home, 15)
    tick(home)
    settle(home, "Ranked work")
    ranker = next(
        c
        for c in fake.calls
        if c["workflow"]["chatThreadTitle"] == f"Rem task: {RANK}"
        and "independent read-only reviewer" not in c["agent_instructions"]
    )
    assert "Save the research report" not in ranker["agent_instructions"]


def test_a_ranking_cannot_touch_tickets_in_another_project(board):
    home, fake, project = board
    roots = more_projects(home, project, "other")
    set_options(home, max_threads=1, auto_pull_backlog=True)
    home.kanban.set_participant_projects(
        "user", {"participant_id": "rem", "projects": [roots[0]], "expected_revision": 2}
    )
    outside = add_backlog(home, "Outside", project_dir=roots[1])
    inside = add_backlog(home, "Inside")
    fake.answers[RANK] = ranking((outside, 5, 5, True, "Bad"), (inside, 3, 3, False, "OK"))
    at(home, 15)
    tick(home)
    settle(home, "Inside")
    untouched = ticket(home, "Outside")
    assert untouched["skip_reason"] == "" and untouched["status"] == "backlog"
    ranker = next(c for c in fake.calls if c["workflow"]["chatThreadTitle"] == f"Rem task: {RANK}")
    assert "Outside" not in " ".join(str(m["body"]) for m in ranker["messages"])


def test_repeated_checks_never_go_over_the_limit(board):
    home, fake, _ = board
    fake.hold.clear()
    set_options(home, max_threads=2, auto_pull_backlog=True)
    for title in ("One", "Two", "Three"):
        add(home, title)
    add_backlog(home, "Backlog")
    at(home, 15)
    for _ in range(6):
        tick(home)
        assert home.self_work.view()["state"]["active_threads"] == 2
    assert ticket(home, "Three")["status"] == "todo"
    fake.hold.set()
    settle(home, "One", "Two", "Three")


def test_lowering_the_limit_starts_nothing_until_there_is_room(board):
    home, fake, _ = board
    fake.hold.clear()
    set_options(home, max_threads=3)
    at(home, 15)
    tick(home)
    assert home.self_work.view()["state"]["active_threads"] == 3
    set_options(home, max_threads=1)
    add(home, "Waits for room")
    tick(home)
    assert ticket(home, "Waits for room")["status"] == "todo"
    assert home.self_work.view()["state"]["active_threads"] == 3
    fake.hold.set()
    settle(home, "Waits for room")
