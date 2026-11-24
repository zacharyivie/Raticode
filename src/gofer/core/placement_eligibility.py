"""Whether one start could run on one paired desktop, and why not, in words.

The placement engine (gofer.core.placement_engine) skips a computer with an
``unusable`` reason. For a paired desktop that reason comes from here, for each start
Rem or an organization places. Every check only reads: the registry's reading of the
desktop (gofer.core.computers) and the catalog it shared with this computer. Nothing
here grants anything; a missing grant only makes the desktop unusable for that start.

A paired desktop is usable for a start only when all of these hold, checked in order,
and the first that fails is the reason, naming the computer:

1. It is paired directly with this computer, never through another desktop.
2. Its status is fresh (online) and it has shared its worker limit.
3. It answered this computer's catalog request with its repository fingerprints. A
   desktop that refuses the request runs an older Raticode.
4. "Run threads from this desktop" is on there for this computer: its catalog lists
   at least one project.
5. It allows the start's provider for this computer's work.
6. Exactly one of its projects has the same repository fingerprint
   (gofer.core.repository_fingerprint) as the start's project here. With two or more,
   the one with the same folder name wins; otherwise none does. A start never falls
   back to another project there.
7. For an organization, the owner made a fleet execution target on that desktop for
   the ticket's worker, for that project. For a Rem task the user pinned there, only
   a research or conversation task runs on another computer for now.

Only a Rem task the user pinned to that desktop runs there (gofer.ui.rem_remote_tasks):
when every check passes it is usable. Any other start that passes every check stays
unusable with ``not_yet``, so the reason says it could.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gofer.core import repository_fingerprint

# The catalog a desktop shared with this computer (gofer.devices.application).
SHARED = "shared"
# It refused the catalog request because it doesn't know the fingerprint field.
UNSUPPORTED = "unsupported"
# It answered the catalog request with an error of another kind.
REFUSED = "refused"


@dataclass(frozen=True)
class Start:
    """What one start needs from a computer.

    ``targets`` is None for Rem. For an organization it holds the organization's
    execution targets, and ``worker_id`` and ``worker_name`` name the ticket's worker.
    ``pinned`` is a Rem task the user pinned to a computer, and ``read_only`` says it
    is a research or conversation task.
    """

    provider: str | None = None
    project_root: str | None = None
    worker_id: str | None = None
    worker_name: str | None = None
    targets: tuple[Mapping[str, Any], ...] | None = None
    pinned: bool = False
    read_only: bool = True

    @property
    def project_name(self) -> str:
        return Path(self.project_root).name if self.project_root else ""


@dataclass(frozen=True)
class Eligibility:
    """One desktop's answer for one start: usable, or why not."""

    usable: bool
    reason: str | None = None
    # The project there that matched the start's, when one did.
    project_id: str | None = None
    project_name: str | None = None


def provider_label(provider: str) -> str:
    from gofer.core.provider_usage import PROVIDER_LABELS

    return PROVIDER_LABELS.get(provider, provider)


def desktop_reason(reading: Mapping[str, Any]) -> str | None:
    """Why no start can run on this paired desktop, whatever it is; None if one might."""
    name = str(reading.get("name") or "Desktop")
    if reading.get("via") or reading.get("paired") is False:
        return f"{name} isn't paired directly with this computer"
    if reading.get("state") != "online":
        return f"{name} is offline"
    if reading.get("max_threads") is None:
        return f"{name} hasn't shared its worker limit"
    catalog = reading.get("catalog")
    if not isinstance(catalog, Mapping):
        return f"Waiting for {name} to share its projects"
    if catalog.get("state") == UNSUPPORTED:
        return f"Update Raticode on {name} so it can say which of its projects match"
    if catalog.get("state") != SHARED or not catalog.get("projects"):
        return f"{name} hasn't allowed this computer to run work there"
    return None


def not_yet(name: str, project: str | None = None) -> str:
    """The reason on a desktop that passed every check: starts don't go there yet."""
    where = f" in its {project} project" if project else ""
    return f"{name} could run this work{where}, but work doesn't start on other computers yet"


def check(
    reading: Mapping[str, Any],
    start: Start,
    fingerprint: Callable[[str | None], str | None] = repository_fingerprint.known,
) -> Eligibility:
    """Whether ``start`` could run on the paired desktop in ``reading``."""
    name = str(reading.get("name") or "Desktop")
    reason = desktop_reason(reading)
    if reason:
        return Eligibility(False, reason)
    catalog: Mapping[str, Any] = reading["catalog"]
    providers = [str(p) for p in catalog.get("providers") or []]
    if start.provider and start.provider not in providers:
        return Eligibility(
            False, f"{name} doesn't allow {provider_label(start.provider)} for this computer's work"
        )
    project = start.project_name
    if not start.project_root:
        return Eligibility(False, f"This work has no project to match on {name}")
    here = fingerprint(start.project_root)
    if here == repository_fingerprint.PENDING:
        return Eligibility(False, f"Checking which project on {name} matches {project}")
    if here is None:
        return Eligibility(
            False, f"{project} isn't a git repository here, so no project on {name} can match it"
        )
    projects: Sequence[Mapping[str, Any]] = catalog.get("projects") or []
    matches = [p for p in projects if p.get("fingerprint") == here]
    if len(matches) > 1:
        # Two clones of one repository: the one in a folder of the same name wins.
        same = [p for p in matches if str(p.get("name") or "").casefold() == project.casefold()]
        if len(same) != 1:
            count = "Two" if len(matches) == 2 else str(len(matches))
            return Eligibility(False, f"{count} projects on {name} match {project}")
        matches = same
    if not matches:
        if any("fingerprint" not in p for p in projects):
            # That desktop answers before it has read every project's repository.
            return Eligibility(False, f"Waiting for {name} to read its projects' repositories")
        return Eligibility(False, f"{name} has no open project matching {project}")
    matched = matches[0]
    project_id = str(matched.get("id") or "")
    there = str(matched.get("name") or project)
    if start.targets is None and start.pinned and not start.read_only:
        return Eligibility(
            False, "Only research and conversation tasks run on other computers for now"
        )
    if start.targets is not None:
        worker = start.worker_name or "this worker"
        mine = [
            t
            for t in start.targets
            if t.get("kind") == "fleet"
            and t.get("deviceId") == reading.get("id")
            and start.worker_id in (t.get("employees") or [])
        ]
        if not mine:
            return Eligibility(False, f"No execution target on {name} is granted to {worker}")
        if not any(t.get("projectId") == project_id for t in mine):
            return Eligibility(
                False, f"{worker}'s execution target on {name} is for another project than {there}"
            )
    return Eligibility(True, None, project_id, there)


def reason_for(
    reading: Mapping[str, Any],
    start: Start | None,
    fingerprint: Callable[[str | None], str | None] = repository_fingerprint.known,
) -> str | None:
    """The ``unusable`` reason a placement snapshot gives this paired desktop, or None.

    The first failed check, or ``not_yet`` when every check passes, except for a Rem
    task the user pinned there, which is usable (None) then. Without a start
    (self-work asking how much room Rem has) only the checks that don't depend on a
    start apply.
    """
    name = str(reading.get("name") or "Desktop")
    if start is None:
        return desktop_reason(reading) or not_yet(name)
    answer = check(reading, start, fingerprint)
    if not answer.usable:
        return str(answer.reason)
    if start.pinned and start.targets is None:
        return None
    return not_yet(name, answer.project_name)
