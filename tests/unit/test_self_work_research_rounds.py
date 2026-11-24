"""Self-work keeps its threads busy through a long session by researching in rounds.

Regression for 13 Nov 2026: in a self-work window that ran for hours, the user's messages never
ended the session, so research ran once per project and angle and self-work then sat at one
thread "out of work" until the window ended. Now a new round starts an hour after the last one
began, as long as the last round found something new. Fake providers and isolated data only.
"""

from typing import Any

from gofer.ui.rem_home import RemHome
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_rem_kanban import set_options, tick, wait_for
from tests.unit.test_rem_pause_resume import reply_to
from tests.unit.test_rem_self_work_windows import Clock, decide
from tests.unit.test_self_work_fill import quiet, worker_titles

ANGLES = (
    "Research new features for project",
    "Research reliability problems in project",
    "Research speed and upkeep in project",
    "Research design and module depth in project",
)


def ideas(*titles: str) -> str:
    body = ",".join(f'{{"title":"{t}","description":"d","why":"w"}}' for t in titles)
    return 'Done.\n```json\n{"proposals":[' + body + '],"duplicates":[]}\n```'


def research_runs(fake: Any) -> list[str]:
    return [t for t in worker_titles(fake) if t.startswith("Research")]


def detail(home: RemHome) -> str:
    return str(home.self_work.view()["state"]["detail"])


def notes(home: RemHome) -> list[str]:
    return [n["text"] for n in home.self_work.view()["report"]["notes"]]


def window(home: RemHome, clock: Clock) -> None:
    """Self-work on for eight hours, starting at once: the user's messages don't end it."""
    set_options(home, enabled=False, max_threads=4)
    decide(
        home,
        "window",
        "enable self-work until 5 PM",
        {"action": "schedule", "end": {"at": clock.at(hours=8)}},
    )


def test_a_long_window_researches_again_after_an_hour_instead_of_running_dry(board):
    home, fake, _ = board
    clock = Clock(home)
    for number, title in enumerate(ANGLES):
        fake.answers[title] = ideas(f"Idea {number} a")
    window(home, clock)
    clock.to(minutes=1)
    tick(home)
    wait_for(lambda: quiet(home), 8)
    assert len(research_runs(fake)) == 4
    assert detail(home).startswith("Out of work for now")
    # The user is around and chatting: in a window that doesn't end the session.
    reply_to(home, "chat", "How's it going?")
    clock.to(minutes=31)
    for _ in range(5):
        tick(home)
    assert len(research_runs(fake)) == 4, "a new round waits an hour after the last began"
    # An hour after the first round began, a new round fills the threads again.
    for number, title in enumerate(ANGLES):
        fake.answers[title] = ideas(f"Idea {number} b")
    clock.to(minutes=62)
    tick(home)
    wait_for(lambda: quiet(home), 8)
    assert len(research_runs(fake)) == 8
    assert any(n.startswith("Started research round 2") for n in notes(home))
    # Each round is its own run, never the earlier round's run again.
    with home.store.read() as db:
        occurrences = [
            row[0]
            for row in db.execute("SELECT occurrence FROM runs WHERE occurrence LIKE 'research:%'")
        ]
    assert len(occurrences) == len(set(occurrences)) == 8


def test_a_round_that_finds_nothing_new_ends_research_for_the_session(board):
    home, fake, _ = board
    clock = Clock(home)
    for number, title in enumerate(ANGLES):
        fake.answers[title] = ideas(f"Only idea {number}")
    window(home, clock)
    clock.to(minutes=1)
    tick(home)
    wait_for(lambda: quiet(home), 8)
    # Round two finds the same ideas again, so it adds nothing new.
    clock.to(minutes=62)
    tick(home)
    wait_for(lambda: quiet(home), 8)
    assert len(research_runs(fake)) == 8
    clock.to(hours=3)
    for _ in range(5):
        tick(home)
    # No third round: research would only repeat itself.
    assert len(research_runs(fake)) == 8
    assert "found nothing new" in detail(home)
    # The note says how many ideas wait for the user, since only they can unlock that work.
    assert "4 ideas wait for your approval" in detail(home)


def test_waiting_proposals_still_stop_new_rounds_while_auto_approval_is_off(board):
    home, fake, _ = board
    clock = Clock(home)
    # Five ideas from each angle: 20 waiting, past the 12 that stop research.
    for number, title in enumerate(ANGLES):
        fake.answers[title] = ideas(*(f"Idea {number}-{i}" for i in range(5)))
    window(home, clock)
    clock.to(minutes=1)
    tick(home)
    wait_for(lambda: quiet(home), 8)
    clock.to(hours=2)
    for _ in range(5):
        tick(home)
    assert len(research_runs(fake)) == 4
    assert detail(home).startswith("Stopped researching")
