"""Each of Rem's projects' level of effort: its share of self-work's threads.

The user gives every project Rem works in on its own a percentage of the thread limit, such
as 70% Raticode and 30% agent-trading. With a limit of 10 that is 7 threads and 3: what counts
is threads held at once, never how many tasks a project finishes, so a project that runs
five short tickets one after another in one thread still holds one.

- Stored in self-work's settings as ``project_effort``: percent by project folder. A project
  taken off Rem's projects keeps its number, so it comes back when the project is chosen
  again; it just doesn't count meanwhile (``percents``).
- The enabled projects' percentages always total 100. One with no number yet gets an even
  share, and the rest scale around it; with no numbers at all the split is even.
- Changing some projects' numbers (``change``) keeps them and rescales the others to fill
  what is left, in the proportions they had.
- ``targets`` turns the percentages into threads: each project gets the whole threads its
  share covers, and the threads left over by rounding go to the projects closest to their
  next one, the largest share first between equally close ones.
- Self-work's threads are the thread limit less the user's own running work, which always
  goes first.
- ``Slots`` is what the scheduler checks before each start. A project below its target may
  start work. One at its target may not, until a pass finds threads still free because
  another project had nothing it could start: then any project may borrow them
  (``lend``). The lender gets them back as the borrowed work finishes: a freed thread goes
  to a project below its target first. Nothing running is stopped to give threads back.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

SETTING = "project_effort"
PERCENT_MAX = 100


class EffortRefused(ValueError):
    """A level-of-effort change that can't be made."""


def _whole(shares: dict[str, float], total: int, order: list[str]) -> dict[str, int]:
    """Whole numbers that add up to ``total``, by largest remainder (``order`` breaks ties)."""
    floors = {root: int(value) for root, value in shares.items()}
    left = total - sum(floors.values())
    ranked = sorted(
        order, key=lambda root: (-(shares[root] - floors[root]), -shares[root], order.index(root))
    )
    for root in ranked[: max(0, left)]:
        floors[root] += 1
    return floors


def _stored(stored: Any) -> dict[str, int]:
    if not isinstance(stored, dict):
        return {}
    return {
        str(root): value
        for root, value in stored.items()
        if type(value) is int and 0 <= value <= PERCENT_MAX
    }


def percents(roots: list[str], stored: Any) -> dict[str, int]:
    """Each enabled project's level of effort in whole percent, totalling 100.

    ``roots`` are the enabled projects in their usual order; ``stored`` the saved numbers,
    which may name projects no longer enabled.
    """
    if not roots:
        return {}
    saved = _stored(stored)
    even = PERCENT_MAX / len(roots)
    # A project with no number yet gets an even share; the others fill the rest in the
    # proportions their numbers give.
    fresh = [root for root in roots if root not in saved]
    rest = PERCENT_MAX - even * len(fresh)
    known = {root: float(saved[root]) for root in roots if root in saved}
    weight = sum(known.values())
    if known and weight <= 0:
        known = dict.fromkeys(known, 1.0)
        weight = float(len(known))
    raw = {root: even if root in fresh else known[root] * rest / weight for root in roots}
    total = float(PERCENT_MAX)
    return _whole({r: v * PERCENT_MAX / total for r, v in raw.items()}, PERCENT_MAX, roots)


def change(roots: list[str], stored: Any, given: Any) -> dict[str, int]:
    """The saved numbers after the user sets ``given`` ({folder: percent}) for some projects.

    The given ones keep their numbers and the other enabled projects share what is left in
    the proportions they had (evenly when they had none). Projects not enabled keep theirs.
    """
    if not isinstance(given, dict) or not given:
        raise EffortRefused("Choose a level of effort for at least one project.")
    for root, value in given.items():
        if root not in roots:
            raise EffortRefused("That project isn't one of Rem's projects right now.")
        if type(value) is not int or not 0 <= value <= PERCENT_MAX:
            raise EffortRefused("Choose a whole percent from 0 to 100.")
    fixed = {str(root): int(value) for root, value in given.items()}
    others = [root for root in roots if root not in fixed]
    used = sum(fixed.values())
    if used > PERCENT_MAX or (not others and used != PERCENT_MAX):
        raise EffortRefused("The levels of effort must add up to 100%.")
    now = percents(roots, stored)
    left = PERCENT_MAX - used
    if others:
        weights = {root: float(now.get(root, 0)) for root in others}
        total = sum(weights.values())
        if total <= 0:
            weights = dict.fromkeys(others, 1.0)
            total = float(len(others))
        fixed.update(_whole({r: w * left / total for r, w in weights.items()}, left, others))
    return {**_stored(stored), **fixed}


def targets(shares: dict[str, int], threads: int) -> dict[str, int]:
    """Threads each project may hold at once out of ``threads``.

    Each gets the whole threads its share covers. The ones rounding leaves go to the projects
    closest to their next thread, and between equally close ones to the largest share: 70/30
    of 3 threads is 2 and 1, and an even split of 3 threads among 3 projects is 1 each. A
    project at 0% gets none of its own, only borrowed ones.
    """
    threads = max(0, int(threads))
    if not shares:
        return {}
    return _whole(
        {root: threads * share / PERCENT_MAX for root, share in shares.items()},
        threads,
        list(shares),
    )


class Slots:
    """The per-project room for new self-work starts during one pass of the scheduler."""

    def __init__(self, targets: dict[str, int], running: dict[str, int]) -> None:
        self.targets = dict(targets)
        self.running = {root: int(running.get(root, 0)) for root in targets}
        # Set once a pass left threads free: then any project may borrow them.
        self.lending = False
        # Projects that had work to start but were at their target this pass.
        self.refused: set[str] = set()

    def allows(self, project: str | None) -> bool:
        """Whether self-work may start work in ``project`` now; records a refusal."""
        if not project or project not in self.targets or self.lending:
            return True
        if self.running[project] < self.targets[project]:
            return True
        self.refused.add(project)
        return False

    def take(self, project: str | None) -> None:
        """A start in ``project`` went ahead: it holds one more thread."""
        if project and project in self.running:
            self.running[project] += 1

    def lend(self) -> bool:
        """Let the next pass give free threads to any project; True when one was held back."""
        self.lending = bool(self.refused)
        return self.lending


def running_by_project(projects: Iterable[str | None], roots: list[str]) -> dict[str, int]:
    """How many threads each project's self-work holds, from the projects of its runs."""
    held = dict.fromkeys(roots, 0)
    for project in projects:
        if project in held:
            held[project] += 1
    return held


def rows(
    roots: list[str],
    shares: dict[str, int],
    goal: dict[str, int],
    held: dict[str, int],
) -> list[dict[str, Any]]:
    """What clients show for each enabled project: its share and threads now versus target."""
    return [
        {
            "root": root,
            "name": Path(root).name or root,
            "effort_percent": shares.get(root, 0),
            "target_threads": goal.get(root, 0),
            "running_threads": held.get(root, 0),
        }
        for root in roots
    ]


def line(listed: list[dict[str, Any]]) -> str:
    """The status line's words for threads by project, or "" with fewer than two projects."""
    if len(listed) < 2:
        return ""
    parts = [f"{row['name']} {row['running_threads']} of {row['target_threads']}" for row in listed]
    return "Threads by project: " + ", ".join(parts) + "."
