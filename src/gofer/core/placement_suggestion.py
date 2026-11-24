"""A paired desktop that could take a waiting start, offered for the user to pick.

When a Rem task or an organization ticket waits because this computer has no room, the
placement snapshot (gofer.core.placement_reader) may hold a paired desktop that has a
free worker and passes every check for that start (gofer.core.placement_eligibility).
``suggest`` finds the first such desktop in the user's order and says so:
"<computer> has room: Run there". Nothing starts there on its own. Work goes there only
when the user picks it:

- A Rem task: only a research or conversation task, the types a pinned task can hand
  over (gofer.ui.rem_remote_tasks). The pick pins the run to that desktop with
  ``chosen_by: user``, and counts as the user's own start.
- An organization ticket: only through a ``fleet`` execution target the owner already
  made on that desktop for the ticket's worker, in that project. The pick sets the
  ticket's ``execution`` to that target, so ``validate_execution`` still applies. A
  desktop that passes every other check but has no such target is suggested with what
  to grant, and offers no pick.

Everything here only reads the snapshot; it grants nothing and starts nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from gofer.core.placement_eligibility import Start, check

if TYPE_CHECKING:
    from gofer.core.placement_reader import Snapshot

RUN_THERE = "Run there"


@dataclass(frozen=True)
class Suggestion:
    """One paired desktop offered for a waiting start."""

    computer_id: str
    computer_name: str
    project_name: str | None = None
    # An organization pick sets the ticket's execution to this owner-made target.
    target_id: str | None = None
    # Set when no pick is offered: what the owner would have to grant first.
    grant: str | None = None

    @property
    def pick(self) -> bool:
        return self.grant is None

    @property
    def text(self) -> str:
        if self.pick:
            return f"{self.computer_name} has room: {RUN_THERE}"
        return f"{self.computer_name} has room. {self.grant}"

    def public(self) -> dict[str, Any]:
        """What a waiting run or ticket keeps and clients show."""
        entry: dict[str, Any] = {
            "computer_id": self.computer_id,
            "computer_name": self.computer_name,
            "project": self.project_name or "",
            "text": self.text,
            "pick": self.pick,
            "grant": self.grant or "",
        }
        if self.target_id:
            entry["target_id"] = self.target_id
        return entry


def owner_target(
    targets: tuple[Mapping[str, Any], ...] | None,
    computer_id: str,
    worker_id: str | None,
    project_id: str | None,
) -> Mapping[str, Any] | None:
    """The owner's fleet execution target on ``computer_id`` for this worker and project."""
    for target in targets or ():
        if (
            target.get("kind") == "fleet"
            and target.get("deviceId") == computer_id
            and worker_id in (target.get("employees") or [])
            and target.get("projectId") == project_id
            and isinstance(target.get("id"), str)
            and target.get("id")
        ):
            return target
    return None


def suggest(snapshot: Snapshot, start: Start) -> Suggestion | None:
    """The first paired desktop, in the user's order, with room for ``start``.

    A desktop that offers a pick wins over one that only says what to grant. None when
    no paired desktop has a free worker and passes the checks.
    """
    granted: Suggestion | None = None
    for computer in snapshot.computers:
        reading = snapshot.desktops.get(computer.id)
        if reading is None or replace(computer, unusable=None).room() <= 0:
            continue
        name = computer.name
        if start.targets is None:
            # Rem: only the task types a pin can hand over to another computer.
            if not start.read_only:
                return None
            answer = check(reading, replace(start, pinned=True), snapshot.fingerprint)
            if answer.usable:
                return Suggestion(computer.id, name, answer.project_name)
            continue
        # An organization: every check but the owner's target first.
        base = check(reading, replace(start, targets=None, pinned=False), snapshot.fingerprint)
        if not base.usable:
            continue
        target = owner_target(start.targets, computer.id, start.worker_id, base.project_id)
        if target is not None:
            return Suggestion(computer.id, name, base.project_name, target_id=str(target["id"]))
        if granted is None:
            worker = start.worker_name or "this worker"
            project = base.project_name or start.project_name
            granted = Suggestion(
                computer.id,
                name,
                base.project_name,
                grant=(
                    f"To run it there, grant {worker} a fleet execution target on {name} "
                    f"for {project}"
                ),
            )
    return granted
