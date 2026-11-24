"""The placement engine: one computer per start, or a wait with its reason."""

from __future__ import annotations

import random
from collections import deque
from collections.abc import Collection
from dataclasses import replace
from fractions import Fraction

import pytest

from gofer.core.placement_engine import (
    BALANCED,
    MANUAL,
    OVERFLOW,
    Computer,
    Consumer,
    PlacementEngineError,
    consumer_from,
    full_reason,
    in_user_order,
    place,
    room_for,
)

# The user's order, and each computer's worker limit.
ORDER = ("c1", "c2", "c3", "c4")
LIMITS = {"c1": 3, "c2": 2, "c3": 2, "c4": 3}
NAMES = {"c1": "Computer 1", "c2": "Computer 2", "c3": "Computer 3", "c4": "Computer 4"}

REM = Consumer("rem", OVERFLOW, ("c1",))
ORG_A = Consumer("org-a", BALANCED, ("c2", "c3"))
ORG_B = Consumer("org-b", MANUAL, ("c4",))
ORG_C = Consumer("org-c", MANUAL, ("c4",))
CONSUMERS = (REM, ORG_A, ORG_B, ORG_C)


SCRIPTED = {
    (2, 8): [ORG_A, ORG_A, ORG_A],
    (4, 10): [REM] * 4 + [ORG_A, ORG_B, ORG_C] * 4,
}


def computers(running: dict[str, int], offline: Collection[str] = ()) -> list[Computer]:
    return [
        Computer(cid, NAMES[cid], LIMITS[cid], running.get(cid, 0), online=cid not in offline)
        for cid in ORDER
    ]


def expected(consumer: Consumer, fleet: list[Computer]) -> str | None:
    """A plain restatement of the spec, to check the engine against at every step."""
    open_ = [c for c in fleet if c.online and c.running < (c.max_threads or 0)]
    mine = [c for c in open_ if c.id in consumer.computers]
    if consumer.strategy == MANUAL:
        return mine[0].id if mine else None
    if consumer.strategy == BALANCED:
        if not mine:
            return None
        low = min(Fraction(c.running, c.max_threads or 1) for c in mine)
        return next(c.id for c in mine if Fraction(c.running, c.max_threads or 1) == low)
    others = [c for c in open_ if c.id not in consumer.computers]
    return (mine + others)[0].id if mine + others else None


def test_simulated_week_places_every_start_within_limits_and_strategy():
    """Rem on computer 1, org A on 2 and 3, orgs B and C sharing 4, for 7 days by hour.

    Org A starts three long runs on day 3 at 08:00, then computer 3 goes offline from
    09:00 to 17:00, interrupting the runs there; they resume on another computer with
    room, or back on computer 3 once it returns. Day 5 at 10:00 brings a burst, Rem's
    four starts first, that overflows Rem and fills every computer.
    """
    rng = random.Random(20261122)
    running: dict[int, tuple[str, str, int]] = {}  # run -> (consumer, computer, hours left)
    queued: deque[tuple[int, Consumer, str | None]] = deque()
    hours: dict[int, int] = {}
    seen = {
        "overflowed": 0,
        "full_waits": 0,
        "offline_skips": 0,
        "returned": 0,
        "moved": 0,
        "balanced_choices": 0,
    }
    shared_both: set[str] = set()
    next_id = 0

    def loads() -> dict[str, int]:
        counts = dict.fromkeys(ORDER, 0)
        for _, computer, _ in running.values():
            counts[computer] += 1
        return counts

    for hour in range(7 * 24):
        day, clock = divmod(hour, 24)
        offline = {"c3"} if day == 2 and 9 <= clock < 17 else set()

        # Runs finish.
        for run_id, (who, computer, left) in list(running.items()):
            if left <= 1:
                del running[run_id]
            else:
                running[run_id] = (who, computer, left - 1)
        # Computer 3 drops: its runs stop and resume, first in line.
        for run_id, (who, computer, _) in list(running.items()):
            if computer in offline:
                del running[run_id]
                consumer = next(c for c in CONSUMERS if c.id == who)
                queued.appendleft((run_id, consumer, computer))

        # New starts arrive: a few an hour, plus the scripted ones.
        for consumer in SCRIPTED.get((day, clock), []):
            queued.append((next_id, consumer, None))
            hours[next_id] = 4
            next_id += 1
        for _ in range(rng.randint(0, 2)):
            queued.append((next_id, rng.choice(CONSUMERS), None))
            next_id += 1

        waiting: deque[tuple[int, Consumer, str | None]] = deque()
        while queued:
            run_id, consumer, previous = queued.popleft()
            fleet = computers(loads(), offline)
            answer = place(consumer, fleet, previous)
            counts = loads()

            if previous is not None and previous not in offline:
                assert answer.computer_id in (previous, None)
            if answer.wait:
                # A wait always says why, naming the computers it passed over.
                assert "Waiting for" in answer.reason
                if previous is None or previous in offline:
                    assert expected(consumer, fleet) is None
                if "is full" in answer.reason:
                    seen["full_waits"] += 1
                waiting.append((run_id, consumer, previous))
                continue

            chosen = answer.computer_id
            assert chosen is not None
            # Never past a worker limit, counting every consumer on the computer.
            assert counts[chosen] < LIMITS[chosen]
            assert chosen not in offline
            assert answer.strategy == consumer.strategy
            if previous is None or previous in offline:
                assert chosen == expected(consumer, fleet), (hour, consumer.id, answer.reason)
            if previous is not None:
                if chosen == previous:
                    seen["returned"] += 1
                    assert "where this run started" in answer.reason
                else:
                    seen["moved"] += 1
                    assert answer.moved and "offline" in answer.moved

            if consumer.strategy == MANUAL:
                assert chosen == "c4"
                shared_both.add(consumer.id)
            if consumer is ORG_A:
                assert chosen in ("c2", "c3")
                seen["balanced_choices"] += 1
                if offline:
                    seen["offline_skips"] += 1
                    assert dict(answer.skipped).get("c3") == "Computer 3 is offline"
            if consumer is REM and chosen != "c1":
                seen["overflowed"] += 1
                assert counts["c1"] == LIMITS["c1"]
                assert "overflowed" in answer.reason
            running[run_id] = (consumer.id, chosen, hours.pop(run_id, 0) or rng.randint(1, 6))
        queued = waiting

        # At every hour the computers are within their limits.
        for computer, count in loads().items():
            assert count <= LIMITS[computer]
        if (day, clock) == (4, 10):
            # The burst filled every computer, and the rest wait with reasons.
            assert loads() == LIMITS
            assert queued

    assert seen["overflowed"] > 0
    assert seen["full_waits"] > 0
    assert seen["offline_skips"] > 0
    assert seen["balanced_choices"] > 0
    assert seen["returned"] + seen["moved"] > 0
    assert shared_both == {"org-b", "org-c"}


def test_overflow_takes_own_computers_first_then_the_users_order():
    fleet = computers({"c1": 3})
    answer = place(REM, fleet)
    assert answer.computer_id == "c2"
    assert answer.reason == "Its own computers are full, so it overflowed to Computer 2"
    assert answer.skipped == (("c1", "Computer 1 is full (3 of 3 running)"),)
    # With room on its own computer it stays there.
    assert place(REM, computers({"c1": 2})).computer_id == "c1"
    # With nothing assigned it walks the user's order.
    assert place(Consumer("rem"), computers({"c1": 3, "c2": 2})).computer_id == "c3"


def test_balanced_takes_the_lowest_share_and_ties_go_by_order():
    assert place(ORG_A, computers({})).computer_id == "c2"
    assert place(ORG_A, computers({"c2": 1})).computer_id == "c3"
    assert place(ORG_A, computers({"c2": 1, "c3": 1})).computer_id == "c2"
    # Lowest running/max, not lowest running: 1 of 4 beats 1 of 2.
    fleet = [
        replace(c, max_threads=4) if c.id == "c3" else c for c in computers({"c2": 1, "c3": 1})
    ]
    answer = place(ORG_A, fleet)
    assert answer.computer_id == "c3"
    assert answer.reason == "Computer 3 has the lightest load (1 of 4 running)"
    # Never a computer outside its assignment, even with room there.
    wait = place(ORG_A, computers({"c2": 2, "c3": 2}))
    assert wait.wait
    assert wait.reason == (
        "Waiting for a free worker: Computer 2 is full (2 of 2 running); "
        "Computer 3 is full (2 of 2 running)"
    )


def test_manual_never_leaves_its_computers_and_counts_shared_load():
    # B and C share computer 4: two B workers and one C worker fill it.
    full = computers({"c4": 3})
    for consumer in (ORG_B, ORG_C):
        answer = place(consumer, full)
        assert answer.wait
        assert answer.reason == "Waiting for a free worker: Computer 4 is full (3 of 3 running)"
    assert place(ORG_C, computers({"c4": 2})).computer_id == "c4"
    offline = place(ORG_B, computers({}, offline={"c4"}))
    assert offline.reason == "Waiting for a free worker: Computer 4 is offline"
    nothing = place(Consumer("x", MANUAL, ()), computers({}))
    assert nothing.reason == "Waiting for a computer: none is assigned to this work"
    unpaired = place(Consumer("x", MANUAL, ("gone",)), computers({}))
    assert unpaired.reason == (
        "Waiting for a computer: none of its computers is available (gone isn't listed)"
    )


def test_offline_unknown_unusable_and_busy_computers_are_skipped():
    fleet = [
        Computer("a", "A", 4, online=False),
        Computer("b", "B", None),
        Computer("c", "C", 4, unusable="Rem tasks can't run on C from this computer yet"),
        Computer("d", "D", 4, running=1, capacity=0),
        Computer("e", "E", 2, running=0, capacity=5),
    ]
    answer = place(Consumer("rem"), fleet)
    assert answer.computer_id == "e"
    assert dict(answer.skipped) == {
        "a": "A is offline",
        "b": "B hasn't shared its worker limit",
        "c": "Rem tasks can't run on C from this computer yet",
        "d": "D is busy (1 of 4 running)",
    }
    # A capacity above the limit never lets a start past it.
    assert place(Consumer("rem"), [Computer("e", "E", 2, running=2, capacity=5)]).wait


def test_resumed_run_returns_to_its_computer_or_is_placed_again_and_says_so():
    back = place(ORG_A, computers({"c2": 0, "c3": 1}), previous="c3")
    assert back.computer_id == "c3"
    assert back.reason == "Back on Computer 3, where this run started"
    assert back.moved is None
    # Only full: it waits there, as its work is on that computer.
    held = place(ORG_A, computers({"c3": 2}), previous="c3")
    assert held.wait
    assert held.reason == (
        "Waiting for a free worker on Computer 3, where this run started: "
        "Computer 3 is full (2 of 2 running)"
    )
    moved = place(ORG_A, computers({}, offline={"c3"}), previous="c3")
    assert moved.computer_id == "c2"
    assert moved.moved == "Computer 3 is offline"
    assert moved.reason.startswith("Computer 3 is offline, so it moved to Computer 2.")
    gone = place(ORG_A, computers({}), previous="old-desktop")
    assert gone.computer_id == "c2"
    assert gone.moved == "The computer this run started on isn't listed anymore"
    unassigned = place(ORG_B, computers({}), previous="c1")
    assert unassigned.computer_id == "c4"
    assert unassigned.moved == "Computer 1 isn't assigned to this work anymore"
    stuck = place(ORG_B, computers({"c4": 3}), previous="c1")
    assert stuck.wait
    assert stuck.reason.startswith(
        "Computer 1 isn't assigned to this work anymore, so it needs another computer."
    )


def test_a_run_whose_work_is_on_its_computer_waits_for_it_and_never_moves():
    # Without stay, an offline computer moves the run (above); with it, the run waits.
    offline = place(ORG_A, computers({}, offline={"c3"}), previous="c3", stay=True)
    assert offline.wait and offline.moved is None
    assert offline.reason == "Waiting for Computer 3 to come back; this run's work is there"
    gone = place(ORG_A, computers({}), previous="old-desktop", stay=True)
    assert gone.wait
    assert gone.reason == ("Waiting for the computer this run started on; it isn't listed anymore")
    unassigned = place(ORG_B, computers({}), previous="c1", stay=True)
    assert unassigned.wait and unassigned.computer_id is None
    assert unassigned.reason.startswith("Waiting for Computer 1, where this run's work is")
    # Back and usable, it goes back there as any resumed run does.
    assert place(ORG_A, computers({}), previous="c3", stay=True).computer_id == "c3"


def test_record_keeps_the_computer_and_strategy_for_review():
    answer = place(REM, computers({}))
    assert answer.record("2026-11-22T10:00:00+00:00") == {
        "computer_id": "c1",
        "computer_name": "Computer 1",
        "strategy": "overflow",
        "consumer": "rem",
        "decided_at": "2026-11-22T10:00:00+00:00",
        "reason": "Computer 1 is the first computer with room",
    }
    assert "moved" in place(ORG_A, computers({}, {"c3"}), "c3").record("t")


def test_bad_inputs_are_refused_and_saved_entries_fall_back_safely():
    with pytest.raises(PlacementEngineError, match="Unknown strategy"):
        place(Consumer("rem", "random"), computers({}))
    with pytest.raises(PlacementEngineError, match="only once"):
        place(REM, [Computer("c1", "A", 1), Computer("c1", "B", 1)])
    assert consumer_from(None, "rem") == Consumer("rem", OVERFLOW, ())
    assert consumer_from({"strategy": "manual", "computers": []}, "rem") == Consumer("rem")
    assert consumer_from({"strategy": "manual", "computers": ["c4", "c4"]}, "b") == Consumer(
        "b", MANUAL, ("c4",)
    )
    assert consumer_from({"strategy": "balanced", "computers": [3]}, "a") == Consumer("a")
    assert consumer_from({"strategy": "balanced"}, "a") == Consumer("a", BALANCED, ())
    ordered = in_user_order(computers({}), ["c3", "c1"])
    assert [c.id for c in ordered] == ["c3", "c1", "c2", "c4"]


def test_room_sums_only_the_computers_a_strategy_may_use_and_names_the_full_ones():
    computers = [
        Computer("c1", "Workstation", 4, 4),
        Computer("c2", "Laptop", 4, 1, capacity=0),
        Computer("c3", "Spare", 4, 0, online=False),
        Computer("c4", "Server", 4, 1),
    ]
    # Overflow may use every computer; manual only its own, never the Server's room.
    assert room_for(Consumer("rem"), computers) == 3
    manual = Consumer("rem", MANUAL, ("c1", "c2", "c3"))
    assert room_for(manual, computers) == 0
    # The offline one isn't full, so only the usable computers are named.
    assert full_reason(manual, computers) == (
        "Computers full: Workstation 4 of 4 running; Laptop busy at 1 of 4 running"
    )
    # With nothing usable at all, the reason is why it can't be placed.
    offline = Consumer("rem", MANUAL, ("c3",))
    assert room_for(offline, computers) == 0
    assert full_reason(offline, computers) == "Waiting for a free worker: Spare is offline"


def test_a_full_or_busy_computers_reason_says_what_fills_it():
    kinds = (("rem", 1), ("phone", 0), ("organizations", 2), ("swarms", 1))
    full = Computer("w", "Workstation", 4, running=4, running_by_kind=kinds)
    answer = place(Consumer("rem"), [full])
    assert answer.wait
    assert answer.reason == (
        "Waiting for a free worker: Workstation is full "
        "(4 of 4 running: 2 organization workers, 1 Rem task, 1 swarm turn)"
    )
    busy = Computer("d", "Desk", 6, running=4, capacity=0, running_by_kind=kinds)
    assert busy.state() == (
        "Desk is busy (4 of 6 running: 2 organization workers, 1 Rem task, 1 swarm turn)"
    )
    # Counts that don't add up to the total (or none, from an older desktop) give the total.
    assert Computer("o", "Old", 2, running=2).state() == "Old is full (2 of 2 running)"
    stale = Computer("s", "Stale", 2, running=2, running_by_kind=(("rem", 1),))
    assert stale.state() == "Stale is full (2 of 2 running)"
