"""Reading a self-work research block one proposal at a time.

A slightly-off block keeps what is usable: long text is cut to its limit, the first five
proposals count, and only a proposal whose title or type can't be used is left out, with
the reason recorded on the research run. Fake providers and isolated data.
"""

import json
from typing import Any

from gofer.ui.rem_self_work import (
    IDEAS_FORMAT,
    PROPOSAL_FORMAT,
    read_proposal,
    read_research,
    research_block,
)
from tests.unit.test_rem_kanban import at, research_run, tick, ticket, ticket_or_none, wait_for
from tests.unit.test_rem_kanban import board as board


def block(proposals: list[Any], **extra: Any) -> str:
    return f"Findings.\n```json\n{json.dumps({'proposals': proposals, **extra})}\n```"


def idea(title: str, **fields: Any) -> dict[str, Any]:
    return {"title": title, "description": f"Build {title}", "why": "Asked for", **fields}


def test_an_over_long_why_is_cut_to_its_limit_and_both_proposals_are_kept():
    result = read_research(block([idea("Voice shortcuts"), idea("Offline mode", why="w" * 1001)]))
    assert result is not None
    assert [p.title for p in result.proposals] == ["Voice shortcuts", "Offline mode"]
    assert result.proposals[1].why == "w" * 1000
    assert result.dropped == []


def test_six_proposals_keep_the_first_five():
    result = read_research(block([idea(f"Idea {n}") for n in range(1, 7)]))
    assert result is not None
    assert [p.title for p in result.proposals] == [f"Idea {n}" for n in range(1, 6)]
    assert result.dropped == []


def test_an_unknown_type_drops_only_that_proposal_with_the_reason():
    result = read_research(
        block([idea("Voice shortcuts"), idea("Dark theme", type="feature"), idea("Sync")])
    )
    assert result is not None
    assert [p.title for p in result.proposals] == ["Voice shortcuts", "Sync"]
    assert len(result.dropped) == 1
    assert result.dropped[0]["title"] == "Dark theme"
    assert result.dropped[0]["reason"].startswith("type:")
    assert "develop" in result.dropped[0]["reason"]


def test_every_text_field_is_cut_to_its_limit_not_refused():
    proposal, reason, repairs = read_proposal(
        idea(
            "t" * 200,
            description="d" * 5000,
            waits_for=["REM-4", 12],
            done_when=[
                f"`pytest` passes {n}{' with every case' * 8}. " + "x " * 200 for n in range(5)
            ],
            type=" Maintain ",
        )
    )
    assert reason == "" and proposal is not None
    assert proposal.title == "t" * 160
    assert proposal.description == "d" * 4000
    assert proposal.waits_for == ["REM-4", "12"]
    # A long finish statement is cut after its last sentence that fits, and that is listed.
    assert proposal.done_when == [f"`pytest` passes {n}{' with every case' * 8}." for n in range(3)]
    assert repairs == [
        "done_when: cut 5 items to 300 characters",
        "done_when: kept the first 3 of 5 items",
    ]
    assert proposal.type == "maintain"


def test_a_bad_optional_field_is_left_out_and_listed_as_a_repair():
    proposal, reason, repairs = read_proposal(
        idea("Voice shortcuts", objective=42, conflicts_with="D-1")
    )
    assert reason == "" and proposal is not None
    assert proposal.objective == "" and proposal.conflicts_with == ["D-1"]
    assert repairs == ["objective: Input should be a valid string; used the default"]
    result = read_research(block([idea("Voice shortcuts", objective=42), idea("Sync")]))
    assert result is not None and result.dropped == []
    assert result.repaired == [
        {
            "title": "Voice shortcuts",
            "reason": "objective: Input should be a valid string; used the default",
        }
    ]


def test_a_conflicts_with_or_waits_for_that_cant_be_read_whole_drops_the_proposal():
    """The decision guard reads conflicts_with and the board's order reads waits_for, so
    neither is emptied to keep a proposal (self-work report, 21 November afternoon)."""
    result = read_research(
        block(
            [
                idea("Named a conflict as an object", conflicts_with=[{"id": "D-4"}]),
                idea("Waits as an object", waits_for={"REM-4": True}),
                idea("Too many conflicts", conflicts_with=[f"D-{n}" for n in range(6)]),
                idea("A cut conflict ID", conflicts_with=["D-" + "9" * 70]),
                idea("Kept", conflicts_with="D-1", waits_for="REM-4"),
            ]
        )
    )
    assert result is not None
    assert [(p.title, p.conflicts_with, p.waits_for) for p in result.proposals] == [
        ("Kept", ["D-1"], ["REM-4"])
    ]
    assert [d["title"] for d in result.dropped] == [
        "Named a conflict as an object",
        "Waits as an object",
        "Too many conflicts",
        "A cut conflict ID",
    ]
    assert [d["reason"].split(":")[0] for d in result.dropped] == [
        "conflicts_with.0",
        "waits_for",
        "conflicts_with",
        "conflicts_with.0",
    ]
    assert result.repaired == []


def test_a_proposal_without_a_usable_title_is_dropped():
    result = read_research(block([{"description": "No name"}, idea("   "), "text", idea("Kept")]))
    assert result is not None
    assert [p.title for p in result.proposals] == ["Kept"]
    assert [d["reason"].split(":")[0] for d in result.dropped] == ["title", "title", "proposal"]


def test_duplicates_are_read_one_at_a_time():
    result = read_research(
        block(
            [],
            duplicates=[
                {"ticket_id": "a", "duplicate_of": "b", "reason": "r" * 600},
                {"ticket_id": "", "duplicate_of": "b"},
                {"ticket_id": "c"},
            ],
        )
    )
    assert result is not None
    assert [(d.ticket_id, d.duplicate_of) for d in result.duplicates] == [("a", "b")]
    assert result.duplicates[0].reason == "r" * 500


def test_the_block_reader_names_the_parsers_error():
    assert research_block("No JSON here.")[1].startswith("The answer has no fenced ```json")
    data, error = research_block('```json\n{"proposals":[{"title":"A",}]}\n```')
    assert data is None and error.startswith("The last ```json block isn't valid JSON:")
    assert "line 1" in error
    assert research_block('```json\n{"proposals":"none"}\n```')[1] == (
        '"proposals" in the ```json block is not a list.'
    )
    assert research_block('```json\n{"other":1}\n```')[1].endswith('no "proposals" list.')
    # An earlier readable block still counts when a later one is something else.
    data, error = research_block(block([idea("A")]) + '\n```json\n{"other":1}\n```')
    assert error == "" and data is not None and data["proposals"][0]["title"] == "A"
    assert read_research("No JSON here.") is None


def test_the_formats_state_the_limits():
    for text in (PROPOSAL_FORMAT, IDEAS_FORMAT):
        assert "only the first 5 are read" in text
        assert "title to 160 characters, why to 1000 and description to 4000" in " ".join(
            text.split()
        )
    assert (
        "waits_for and conflicts_with must each be a list of plain text IDs or titles "
        "(at most 10 and 5), or the proposal is dropped"
    ) in " ".join(PROPOSAL_FORMAT.split())


def test_a_dropped_proposal_is_recorded_on_the_run_while_the_others_are_filed(board):
    home, fake, _ = board
    title = "Research new features for project"
    fake.answers[title] = block(
        [
            idea("Voice shortcuts"),
            idea("Dark theme", type="feature"),
            idea("Offline sync", objective=42),
        ]
    )
    at(home, 15)
    tick(home)
    filed = ("Voice shortcuts", "Offline sync")
    wait_for(lambda: not tick(home) and all(ticket_or_none(home, t) for t in filed), 8)
    assert all(ticket(home, t)["proposed"] for t in filed)
    assert ticket_or_none(home, "Dark theme") is None
    run = research_run(home, title)
    assert run is not None
    rejected = run["rejected_proposals"]
    assert [r["title"] for r in rejected] == ["Dark theme"]
    assert rejected[0]["reason"].startswith("type:") and rejected[0]["at"]
    notes = [n["text"] for n in home.self_work.view()["report"]["notes"]]
    assert any(n.startswith("Didn't add “Dark theme”: type:") for n in notes)
    # A proposal filed without a field it couldn't use says so on the run and in the report.
    assert [(r["title"], r["reason"]) for r in run["repaired_proposals"]] == [
        ("Offline sync", "objective: Input should be a valid string; used the default")
    ]
    assert "Used the proposal “Offline sync” only in part: objective:" in " ".join(notes)
    work = home.self_work.view()["report"]["work_done"]
    assert any(
        w["title"] == title and "Dropped 1 proposal that couldn't be read whole" in w["summary"]
        for w in work
    )
