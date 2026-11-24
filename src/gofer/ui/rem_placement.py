"""Where Rem's runs start: the placement engine wired to Rem's admission.

Every Rem run (a task, self-work, a run started from chat) is admitted in
``RemService.tick``, which asks ``place`` for a computer before it starts a worker.
The computers come from the registry through the shared reader
(gofer.core.placement_reader) with every consumer's workers on this computer counted
live from the worker claims (gofer.core.worker_claims), so Rem never takes a computer
past its worker limit when organization workers or phone turns share it, and starts
nothing while the computer is short of memory with a worker running there (the
claims' memory hold, which every start shares). The tick
takes its worker with ``Snapshot.claim`` right before it starts one, so a start on
another thread can't take the same last worker. Each paired desktop is checked for
each start (``start_for`` and gofer.core.placement_eligibility): pairing, status,
worker limit, the projects and providers it shared, and a project there with the same
repository fingerprint as the run's. Rem's workers run only on this computer, so a
paired desktop stays unusable, with the first check it failed as the reason, and a
start that has nowhere to go waits with the engine's reason. The one exception is a
research or conversation task the user pinned to a paired desktop (its ``computer``):
it is placed only there, and when every check passes the whole task is handed over and
runs as that desktop's own Rem task (gofer.ui.rem_remote_tasks).

A research or conversation task that waits for room, when a paired desktop has room
and passes every check for it, keeps that desktop as its ``suggestion``
(``run_there_suggestion``): "<computer> has room: Run there". Nothing goes there
until the user picks it (``RemHome.run_there``), which pins the run there.

Rem's strategy and assigned computers are its ``rem`` entry in the placement
settings (gofer.core.placement_settings); with no entry Rem overflows across every
computer in the registry's order (this computer first) unless the saved order says
otherwise.

The registry is read outside Rem's lock, at most once a second and only while a run
is waiting for placement (``RemService.tick`` calls ``snapshot`` only for a queued
run it is about to place), because it reads paired desktops under the device
service's lock. While no reading can be used, the waiting run shows why.

Self-work reads the same snapshot before it starts new work (``Snapshot.room``), so it
moves no more tickets to In progress than Rem's computers have room for, and starts
nothing without a fresh reading, as admission does.
"""

from __future__ import annotations

from typing import Any

from gofer.core import placement_settings
from gofer.core.placement_eligibility import Start
from gofer.core.placement_engine import Computer, consumer_from
from gofer.core.placement_reader import PlacementReader, Snapshot
from gofer.core.placement_suggestion import suggest
from gofer.core.worker_claims import THIS_COMPUTER

__all__ = [
    "LOCAL_ONLY_ID",
    "REM",
    "RemPlacement",
    "Snapshot",
    "pinned_computer",
    "previous_computer",
    "run_there_suggestion",
    "start_for",
]

REM = placement_settings.REM
LOCAL_ONLY_ID = THIS_COMPUTER


class RemPlacement(PlacementReader):
    """Reads the registry for Rem's admission and builds each pass's snapshot."""

    def snapshot(self, worker_limit: int) -> Snapshot | None:
        """This pass's computers, or None until a fresh registry reading is in.

        Call it only when a queued run needs placing, or self-work is about to start
        work: it asks ``refresh`` for a reading. When it returns None, ``wait_reason``
        says why once that lasts. Without the registry (tests, or before the UI
        server sets it) Rem uses this computer alone with ``worker_limit``.
        """
        if self.computers is None:
            self.ask()
            running = self.claims.held(LOCAL_ONLY_ID)
            return self.ready(
                Snapshot(
                    consumer_from(None, REM),
                    [
                        Computer(
                            LOCAL_ONLY_ID,
                            "This computer",
                            worker_limit,
                            running,
                            memory_hold=self.claims.memory_hold(LOCAL_ONLY_ID),
                        )
                    ],
                )
            )
        # Every consumer's workers on this computer are counted live from the claims.
        return self.registry_snapshot(REM)


def start_for(run: dict[str, Any]) -> Start:
    """What a run's start needs from a computer: its provider and its project.

    A task the user pinned to a computer (its ``computer``) is ``pinned``; only a
    research or conversation task can run on a paired desktop.
    """
    from gofer.ui.rem_home_models import read_only_recipe, worker_config

    config: dict[str, Any] = run["config"] if isinstance(run.get("config"), dict) else {}
    recipe: dict[str, Any] = run["recipe"] if isinstance(run.get("recipe"), dict) else {}
    provider = worker_config(config, recipe).get("provider") or "codex"
    project = recipe.get("project_dir")
    return Start(
        provider=str(provider),
        project_root=str(project) if isinstance(project, str) and project else None,
        pinned=bool(pinned_computer(run)),
        read_only=read_only_recipe(recipe),
    )


def run_there_suggestion(snapshot: Snapshot | None, run: dict[str, Any]) -> dict[str, Any] | None:
    """The paired desktop offered to a run that waits for room, as the run keeps it.

    Only for a run nobody pinned yet, and only a research or conversation task, the
    types a pin hands over (gofer.core.placement_suggestion). None otherwise.
    """
    if snapshot is None or pinned_computer(run) or run.get("remote"):
        return None
    found = suggest(snapshot, start_for(run))
    return found.public() if found is not None and found.pick else None


def pinned_computer(run: dict[str, Any]) -> str | None:
    """The computer the user pinned this run's task to, if any."""
    recipe = run.get("recipe")
    value = recipe.get("computer") if isinstance(recipe, dict) else None
    return value if isinstance(value, str) and value else None


def previous_computer(run: dict[str, Any]) -> str | None:
    """The computer a run that started before was placed on."""
    placement = run.get("placement")
    value = placement.get("computer_id") if isinstance(placement, dict) else None
    return value if isinstance(value, str) and value else None
