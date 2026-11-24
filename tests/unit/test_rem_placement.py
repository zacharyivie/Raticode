"""Rem's admission asks the placement engine for each run's computer."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, TypeVar

import pytest

from gofer.core import placement_engine, placement_settings, repository_fingerprint
from gofer.core.placement_eligibility import Start, check
from gofer.core.placement_reader import PlacementReader
from gofer.core.worker_claims import Claim, WorkerClaims
from gofer.ui.rem_placement import RemPlacement, pinned_computer, previous_computer, start_for

# The repository fingerprint of the project a start works in here.
FP = "a" * 64
OTHER = "b" * 64

P = TypeVar("P", bound=PlacementReader)


class FakeComputers:
    """The registry's view: this computer shared with organization workers, and a desktop.

    The organization workers hold real worker claims in ``claims``, which placement
    counts live, as the server shares one set of claims with every consumer; a test
    gives them to its placement with ``attach``.
    """

    def __init__(
        self,
        organizations: int = 1,
        limit: int = 2,
        desktop: str = "online",
        catalog: dict[str, Any] | None = None,
    ) -> None:
        self.claims = WorkerClaims()
        self.held: list[Claim] = []
        self.organizations = organizations
        self.limit = limit
        self.desktop = desktop
        # What Desktop B shared with this computer; None until it answers.
        self.catalog = catalog
        self.reads = 0

    @property
    def organizations(self) -> int:
        return len(self.held)

    @organizations.setter
    def organizations(self, count: int) -> None:
        while len(self.held) > count:
            self.held.pop().release()
        while len(self.held) < count:
            self.held.append(self.claims.record("pc-1", "organizations"))

    def attach(self, placement: P) -> P:
        placement.computers = self
        placement.claims = self.claims
        return placement

    def read(self) -> dict[str, Any]:
        self.reads += 1
        kinds = self.claims.running("pc-1")
        running = sum(kinds.values())
        return {
            "computers": [
                {
                    "id": "pc-1",
                    "name": "Workstation",
                    "this_computer": True,
                    "state": "online",
                    "max_threads": self.limit,
                    "running_threads": running,
                    "cpu_pressure": None,
                    "memory_pressure": None,
                    "capacity": max(0, self.limit - running),
                    "running_by_kind": kinds,
                },
                {
                    "id": "desk-b",
                    "name": "Desktop B",
                    "this_computer": False,
                    "state": self.desktop,
                    "max_threads": 4,
                    "running_threads": 0,
                    "capacity": 4 if self.desktop == "online" else 0,
                    "catalog": self.catalog,
                },
            ]
        }


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def ready(tmp_path: Path, fake: FakeComputers, clock: Clock) -> RemPlacement:
    placement = fake.attach(RemPlacement(tmp_path, clock=clock))
    assert placement.snapshot(2) is None  # asks for a reading first
    placement.refresh()
    return placement


def test_rem_counts_other_consumers_and_never_runs_on_a_desktop(tmp_path: Path):
    clock = Clock()
    fake = FakeComputers(organizations=1, limit=2)
    placement = ready(tmp_path, fake, clock)
    snapshot = placement.snapshot(2)
    assert snapshot is not None
    first = snapshot.place()
    assert (first.computer_id, first.strategy) == ("pc-1", "overflow")
    snapshot.started("pc-1")
    # The organization worker and Rem's new one fill the limit of 2.
    second = snapshot.place()
    assert second.wait
    assert second.reason == (
        "Waiting for a free worker: Workstation is full (2 of 2 running); "
        "Waiting for Desktop B to share its projects"
    )
    # Every count is live from the claims: one running Rem worker on top of the
    # organization's fills it, and it is free again as soon as that worker ends.
    rem = placement.claims.record("pc-1", "rem")
    assert placement.snapshot(2).place().wait
    rem.release()
    assert placement.snapshot(2).place().computer_id == "pc-1"
    # An organization worker that ended is not counted, though the reading still has it.
    fake.organizations = 0
    assert placement.snapshot(2).room() == 2


def test_reads_only_while_a_run_waits_and_never_from_a_stale_reading(tmp_path: Path):
    clock = Clock()
    fake = FakeComputers()
    placement = ready(tmp_path, fake, clock)
    assert fake.reads == 1
    placement.refresh()  # nothing asked since the last read
    assert fake.reads == 1
    placement.snapshot(2)
    clock.now += 0.5
    placement.refresh()  # too soon
    assert fake.reads == 1
    clock.now += 0.6
    placement.refresh()
    assert fake.reads == 2
    clock.now += 10
    assert placement.snapshot(2) is None  # stale: wait for a fresh one


def test_saved_strategy_is_used_and_a_manual_desktop_only_choice_waits(tmp_path: Path):
    fake = FakeComputers(organizations=0)
    placement_settings.save(
        tmp_path,
        "rem",
        "manual",
        ["desk-b"],
        known_computers=fake.read()["computers"],
        organizations=[],
    )
    placement = ready(tmp_path, fake, Clock())
    answer = placement.snapshot(2).place()
    assert answer.strategy == "manual"
    assert answer.reason == "Waiting for a free worker: Waiting for Desktop B to share its projects"


def test_without_a_registry_rem_uses_this_computer_and_its_worker_limit(tmp_path: Path):
    placement = RemPlacement(tmp_path)
    assert placement.snapshot(1).place().computer_id == "this"
    placement.claims.record("this", "rem")
    full = placement.snapshot(1).place()
    assert full.reason == "Waiting for a free worker: This computer is full (1 of 1 running)"


def test_previous_computer_reads_the_runs_record():
    assert previous_computer({}) is None
    assert previous_computer({"placement": {"computer_id": "pc-1"}}) == "pc-1"
    assert previous_computer({"placement": "bad"}) is None


def test_saved_order_and_balanced_strategy_reach_the_engine(tmp_path: Path):
    fake = FakeComputers(organizations=0)
    placement_settings.save(
        tmp_path, "rem", "balanced", known_computers=fake.read()["computers"], organizations=[]
    )
    placement = ready(tmp_path, fake, Clock())
    snapshot = placement.snapshot(2)
    assert snapshot.consumer.strategy == "balanced"
    assert [c.id for c in snapshot.computers] == ["pc-1", "desk-b"]


def test_engine_and_settings_agree_on_strategies():
    assert placement_engine.STRATEGIES == placement_settings.STRATEGIES
    assert placement_engine.DEFAULT_STRATEGY == placement_settings.DEFAULT_STRATEGY


class BrokenComputers(FakeComputers):
    def read(self) -> dict[str, Any]:
        self.reads += 1
        raise OSError("computers.json is unreadable")


class NoLocalComputers(FakeComputers):
    def read(self) -> dict[str, Any]:
        view = super().read()
        view["computers"] = [c for c in view["computers"] if not c["this_computer"]]
        return view


def test_a_failed_read_shows_its_error_at_once(tmp_path: Path):
    placement = BrokenComputers().attach(RemPlacement(tmp_path, clock=Clock()))
    assert placement.snapshot(2) is None
    assert placement.wait_reason() is None  # the first pass waits silently
    placement.refresh()
    assert placement.snapshot(2) is None
    assert placement.wait_reason() == (
        "Waiting for the computers' reading: computers.json is unreadable"
    )
    # A failing read is retried once a second, not on every pass.
    placement.snapshot(2)
    placement.refresh()
    assert placement.computers.reads == 1


def test_a_lasting_missing_or_stale_reading_shows_its_cause(tmp_path: Path):
    clock = Clock()
    placement = ready(tmp_path, NoLocalComputers(), clock)
    assert placement.snapshot(2) is None
    assert placement.wait_reason() is None
    clock.now += 1.1
    placement.refresh()
    assert placement.snapshot(2) is None
    clock.now += 1.0
    assert placement.snapshot(2) is None
    assert placement.wait_reason() == (
        "Waiting for the computers' reading: it doesn't list this computer"
    )

    stale = ready(tmp_path, FakeComputers(), clock)
    assert stale.snapshot(2) is not None
    # A run after a quiet spell: the old reading is stale for one silent pass.
    clock.now += 10
    assert stale.snapshot(2) is None
    assert stale.wait_reason() is None
    # No refresh happens, so the reading stays stale.
    for _ in range(3):
        clock.now += 1.0
        assert stale.snapshot(2) is None
    assert stale.wait_reason() is not None
    assert stale.wait_reason().startswith("Waiting for a fresh reading of the computers")
    # Once a reading can be used again, nothing is left over.
    stale.computers = FakeComputers()
    stale.refresh()
    assert stale.snapshot(2) is not None
    assert stale.wait_reason() is None


def shared(*projects: dict[str, Any], providers: tuple[str, ...] = ("codex",)) -> dict[str, Any]:
    return {"state": "shared", "projects": list(projects), "providers": list(providers)}


RATICODE = {"id": "p-1", "name": "Raticode", "fingerprint": FP}
DESKTOP = {
    "id": "desk-b",
    "name": "Desktop B",
    "this_computer": False,
    "state": "online",
    "max_threads": 4,
    "catalog": shared(RATICODE),
}
START = Start(provider="codex", project_root="/home/me/code/Raticode")


def fingerprints(root: str | None) -> str | None:
    return {"/home/me/code/Raticode": FP, "/home/me/notes": None}.get(str(root), OTHER)


@pytest.mark.parametrize(
    "change,start,reason",
    [
        ({"via": "desk-c"}, START, "Desktop B isn't paired directly with this computer"),
        ({"state": "offline"}, START, "Desktop B is offline"),
        ({"max_threads": None}, START, "Desktop B hasn't shared its worker limit"),
        ({"catalog": None}, START, "Waiting for Desktop B to share its projects"),
        (
            {"catalog": {"state": "unsupported"}},
            START,
            "Update Raticode on Desktop B so it can say which of its projects match",
        ),
        (
            {"catalog": {"state": "refused"}},
            START,
            "Desktop B hasn't allowed this computer to run work there",
        ),
        (
            {},
            replace(START, provider="claude_code"),
            "Desktop B doesn't allow Claude Code for this computer's work",
        ),
        ({}, replace(START, project_root=None), "This work has no project to match on Desktop B"),
        (
            {},
            replace(START, project_root="/home/me/notes"),
            "notes isn't a git repository here, so no project on Desktop B can match it",
        ),
        (
            {"catalog": shared({**RATICODE, "fingerprint": OTHER})},
            START,
            "Desktop B has no open project matching Raticode",
        ),
        (
            {
                "catalog": shared(
                    {**RATICODE, "name": "rc-a"}, {**RATICODE, "id": "p-2", "name": "rc-b"}
                )
            },
            START,
            "Two projects on Desktop B match Raticode",
        ),
        (
            {"catalog": shared({"id": "p-3", "name": "Raticode"})},
            START,
            "Waiting for Desktop B to read its projects' repositories",
        ),
    ],
)
def test_each_failed_check_gives_its_own_reason_naming_the_computer(change, start, reason):
    answer = check({**DESKTOP, **change}, start, fingerprints)
    assert not answer.usable
    assert answer.reason == reason
    assert "Desktop B" in reason


def test_reasons_are_distinct_for_every_check():
    cases = test_each_failed_check_gives_its_own_reason_naming_the_computer.pytestmark[0].args[1]
    reasons = [reason for _, _, reason in cases]
    assert len(set(reasons)) == len(reasons)
    # Refusing with no projects is the same check as an error: hosting is off there.
    empty = check({**DESKTOP, "catalog": shared()}, START, fingerprints)
    assert empty.reason == "Desktop B hasn't allowed this computer to run work there"


def test_a_desktop_is_usable_only_when_every_check_passes():
    answer = check(DESKTOP, START, fingerprints)
    assert answer.usable and answer.reason is None
    assert (answer.project_id, answer.project_name) == ("p-1", "Raticode")
    # Two clones there: the one in a folder of the same name wins, never the first.
    clones = shared({**RATICODE, "id": "p-9", "name": "raticode-old"}, {**RATICODE, "id": "p-2"})
    answer = check({**DESKTOP, "catalog": clones}, START, fingerprints)
    assert answer.usable and answer.project_id == "p-2"
    # A project there still being read doesn't match yet, and says so while it is read here.
    reading = check(DESKTOP, START, lambda root: repository_fingerprint.PENDING)
    assert reading.reason == "Checking which project on Desktop B matches Raticode"


def test_start_for_reads_the_runs_provider_and_project():
    run = {"config": {"provider": "claude_code"}, "recipe": {"project_dir": "/p/Raticode"}}
    assert start_for(run) == Start(
        provider="claude_code", project_root="/p/Raticode", read_only=False
    )
    assert start_for({"config": {}, "recipe": {}}) == Start(
        provider="codex", project_root=None, read_only=False
    )
    # The task's own provider wins, and a pin to a computer marks the start pinned.
    pinned = {
        "config": {"provider": "codex"},
        "recipe": {"type": "research", "provider": "claude_code", "computer": "desk-b"},
    }
    assert start_for(pinned) == Start(provider="claude_code", pinned=True, read_only=True)
    assert pinned_computer(pinned) == "desk-b" and pinned_computer(run) is None


def manual_on_desktop(tmp_path: Path, fake: FakeComputers) -> RemPlacement:
    placement_settings.save(
        tmp_path,
        "rem",
        "manual",
        ["desk-b"],
        known_computers=fake.read()["computers"],
        organizations=[],
    )
    return ready(tmp_path, fake, Clock())


def test_a_placed_start_with_no_matching_project_there_waits_and_never_goes_elsewhere(
    tmp_path: Path,
):
    # Desktop B shares one project, but it is another repository with the same name.
    fake = FakeComputers(organizations=0, catalog=shared({**RATICODE, "fingerprint": OTHER}))
    placement = manual_on_desktop(tmp_path, fake)
    snapshot = placement.snapshot(2)
    assert snapshot is not None
    snapshot.fingerprint = fingerprints
    answer = snapshot.place(start=START)
    assert answer.wait and answer.computer_id is None
    assert answer.reason == (
        "Waiting for a free worker: Desktop B has no open project matching Raticode"
    )
    # Overflow can use this computer, and never Desktop B's other project.
    placement_settings.save(
        tmp_path, "rem", "overflow", known_computers=fake.read()["computers"], organizations=[]
    )
    tick = ready(tmp_path, fake, Clock()).snapshot(2)
    assert tick is not None
    tick.fingerprint = fingerprints
    assert tick.place(start=START).computer_id == "pc-1"


def test_a_desktop_that_passes_every_check_still_runs_nothing(tmp_path: Path):
    fake = FakeComputers(organizations=0, catalog=shared(RATICODE))
    placement = manual_on_desktop(tmp_path, fake)
    snapshot = placement.snapshot(2)
    snapshot.fingerprint = fingerprints
    answer = snapshot.place(start=START)
    assert answer.wait
    assert answer.reason == (
        "Waiting for a free worker: Desktop B could run this work in its Raticode project, "
        "but work doesn't start on other computers yet"
    )
    # Self-work asks for room without a start: Desktop B adds none.
    assert snapshot.room() == 0


PINNED = replace(START, pinned=True)


def test_a_research_task_pinned_to_a_desktop_is_placed_only_there(tmp_path: Path):
    # Rem overflows; this computer has room, yet the pin sends the task to Desktop B.
    fake = FakeComputers(organizations=0, catalog=shared(RATICODE))
    snapshot = ready(tmp_path, fake, Clock()).snapshot(2)
    assert snapshot is not None
    snapshot.fingerprint = fingerprints
    assert snapshot.place(start=PINNED).computer_id == "pc-1"
    pinned = snapshot.pinned("desk-b")
    answer = pinned.place(start=PINNED)
    assert (answer.computer_id, answer.strategy) == ("desk-b", "manual")
    assert pinned.desktop(answer.computer_id) and not pinned.desktop("pc-1")
    matched = pinned.eligibility("desk-b", PINNED)
    assert (matched.project_id, matched.project_name) == ("p-1", "Raticode")
    # A develop task never runs there, and says why.
    develop = pinned.place(start=replace(PINNED, read_only=False))
    assert develop.wait
    assert develop.reason == (
        "Waiting for a free worker: "
        "Only research and conversation tasks run on other computers for now"
    )
    # A failed check still makes it wait there, never run here instead.
    elsewhere = pinned.place(start=replace(PINNED, provider="claude_code"))
    assert elsewhere.wait and elsewhere.computer_id is None
    assert "doesn't allow Claude Code" in elsewhere.reason
    # Pinned to this computer, it runs here as usual.
    assert snapshot.pinned("pc-1").place(start=PINNED).computer_id == "pc-1"


def test_a_stand_in_for_a_task_on_a_desktop_holds_no_thread_for_self_work():
    from datetime import UTC, datetime

    from gofer.ui.rem_self_work import _holds_thread

    now = datetime.now(UTC)
    running = {"state": "executing"}
    assert _holds_thread(running, now)
    # Self-work doesn't make way for a task that runs on a paired desktop.
    assert not _holds_thread({**running, "remote": {"computer_id": "desk-b"}}, now)


def test_a_run_that_started_on_a_desktop_waits_for_it_while_it_is_offline(tmp_path: Path):
    fake = FakeComputers(organizations=0, desktop="offline", catalog=shared(RATICODE))
    snapshot = ready(tmp_path, fake, Clock()).snapshot(2)
    assert snapshot is not None
    snapshot.fingerprint = fingerprints
    # Overflow would move it to this computer, which has room; its work is on Desktop B.
    answer = snapshot.place("desk-b", PINNED)
    assert answer.wait and answer.moved is None
    assert answer.reason == "Waiting for Desktop B to come back; this run's work is there"


def git_repository(root: Path, message: str = "first") -> Path:
    root.mkdir(parents=True)
    for args in (
        ["init", "-q"],
        [
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@example.invalid",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            message,
        ],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    return root


def test_fingerprint_is_the_same_for_every_clone_and_hides_the_path(tmp_path: Path):
    repository_fingerprint.forget()
    first = git_repository(tmp_path / "one" / "Raticode")
    clone = tmp_path / "two" / "copy"
    subprocess.run(["git", "clone", "-q", str(first), str(clone)], check=True, capture_output=True)
    value = repository_fingerprint.fingerprint(first)
    assert value is not None and len(value) == 64
    assert repository_fingerprint.fingerprint(clone) == value
    root = subprocess.run(
        ["git", "-C", str(first), "rev-list", "--max-parents=0", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert value == repository_fingerprint.from_root_commits(root)
    assert str(tmp_path) not in value
    other = git_repository(tmp_path / "three" / "Raticode", "another first commit")
    assert repository_fingerprint.fingerprint(other) != value
    plain = tmp_path / "plain"
    plain.mkdir()
    assert repository_fingerprint.fingerprint(plain) is None
    assert repository_fingerprint.known(first) == value  # kept, so no git runs here
