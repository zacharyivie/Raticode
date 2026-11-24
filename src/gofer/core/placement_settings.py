"""Placement settings: which computers Rem and each organization run their workers on.

Each consumer (Rem, and each organization) has a strategy:

- ``overflow`` fills one computer up to its worker limit, then the next.
- ``balanced`` spreads workers evenly across the computers that still have room.
- ``manual`` uses only the computers the user assigned to it, one or more.

A computer can be in several manual lists only on purpose: every consumer that has
it must mark it shared. So Rem can have one computer, organization A two others, and
organizations B and C can share a fourth, while A's computers stay A's alone.

The settings live in the computer registry's store (``computers.json`` in the data
folder, under ``placement``) as ``{"order": [computer IDs], "consumers": {ID:
{"strategy", "computers", "shared"}}}``. Rem's consumer ID is ``rem``; an
organization's is ``org:<organization ID>``. ``order`` is the user's order of
computers, which overflow fills in turn; computers it doesn't name come after, in
the registry's order. Saving checks each computer against the registry and each
organization against the ones that exist. A computer unpaired after it was assigned
stays in the list and shows in ``missing_computers``; a deleted organization stops
showing and is dropped at the next save. This module only keeps the settings;
choosing a computer reads them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from gofer.core import computers as registry

STRATEGIES = ("overflow", "balanced", "manual")
DEFAULT_STRATEGY = "overflow"
REM = "rem"
ORGANIZATION_PREFIX = "org:"
# A manual list longer than this is a mistake; Raticode pairs far fewer computers.
MAX_COMPUTERS = 64


class PlacementError(ValueError):
    """A placement the settings refuse, with the reason in words."""


def organization_consumer(organization_id: str) -> str:
    return f"{ORGANIZATION_PREFIX}{organization_id}"


def _ids(value: Any, field: str) -> list[str]:
    """A list of computer IDs, in order, without repeats."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PlacementError(f"{field} must be a list of computer IDs")
    seen: list[str] = []
    for item in value:
        item = item.strip()
        if not item:
            raise PlacementError(f"{field} has an empty computer ID")
        if item not in seen:
            seen.append(item)
    if len(seen) > MAX_COMPUTERS:
        raise PlacementError(f"{field} can list at most {MAX_COMPUTERS} computers")
    return seen


def _stored(entry: Any) -> dict[str, Any] | None:
    """A saved entry as the settings read it, or None when it can't be read."""
    if not isinstance(entry, Mapping) or entry.get("strategy") not in STRATEGIES:
        return None
    try:
        computers = _ids(entry.get("computers"), "computers")
        shared = _ids(entry.get("shared"), "shared")
    except PlacementError:
        return None
    if entry["strategy"] != "manual":
        return {"strategy": entry["strategy"], "computers": [], "shared": []}
    if not computers:
        return None
    return {
        "strategy": "manual",
        "computers": computers,
        "shared": [c for c in shared if c in computers],
    }


def _placement(store: Mapping[str, Any]) -> tuple[list[Any], dict[str, dict[str, Any]]]:
    """The saved ``order`` as it is, and each readable consumer entry by ID."""
    value = store.get("placement")
    value = value if isinstance(value, Mapping) else {}
    order = value.get("order")
    entries: dict[str, dict[str, Any]] = {}
    consumers = value.get("consumers")
    if isinstance(consumers, Mapping):
        for key, entry in consumers.items():
            stored = _stored(entry) if isinstance(key, str) and key else None
            if stored is not None:
                entries[key] = stored
    return (list(order) if isinstance(order, list) else []), entries


def _organizations(organizations: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Consumer ID to name for each organization."""
    names: dict[str, str] = {}
    for item in organizations:
        oid = item.get("id")
        if isinstance(oid, str) and oid:
            names[organization_consumer(oid)] = str(item.get("name") or "Organization")
    return names


def _computers(computers: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {str(c["id"]): c for c in computers if isinstance(c.get("id"), str) and c["id"]}


def _name(consumer: str, names: Mapping[str, str]) -> str:
    return "Rem" if consumer == REM else names.get(consumer, "an organization")


def validate(
    consumer: Any,
    strategy: Any,
    computers: Any = None,
    shared: Any = None,
    *,
    known_computers: Iterable[Mapping[str, Any]],
    organizations: Iterable[Mapping[str, Any]],
    others: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Check one consumer's placement and return the entry as it would be saved.

    ``others`` holds the saved entries by consumer ID, for the sharing rule: a
    computer in two manual lists needs every consumer that has it to mark it shared.
    """
    names = _organizations(organizations)
    if not isinstance(consumer, str) or not consumer.strip():
        raise PlacementError("Send consumer: rem or org:<organization ID>")
    consumer = consumer.strip()
    if consumer != REM and consumer not in names:
        raise PlacementError(f"No consumer {consumer}; use rem or org:<organization ID>")
    if strategy not in STRATEGIES:
        raise PlacementError(f"strategy must be one of {', '.join(STRATEGIES)}")
    chosen = _ids(computers, "computers")
    marked = _ids(shared, "shared")
    if strategy != "manual":
        if chosen or marked:
            raise PlacementError(f"Only manual placement takes computers; {strategy} picks them")
        return {"strategy": strategy, "computers": [], "shared": []}
    if not chosen:
        raise PlacementError("Manual placement needs at least one computer")
    known = _computers(known_computers)
    unknown = [c for c in chosen if c not in known]
    if unknown:
        raise PlacementError(f"No computer with the ID {unknown[0]}")
    outside = [c for c in marked if c not in chosen]
    if outside:
        raise PlacementError(f"Shared computer {outside[0]} isn't one of this placement's")
    for computer in chosen:
        label = str(known[computer].get("name") or computer)
        for key, entry in others.items():
            if key == consumer or (key != REM and key not in names):
                continue
            if entry.get("strategy") != "manual" or computer not in entry.get("computers", []):
                continue
            if computer not in marked:
                raise PlacementError(
                    f"{label} is already assigned to {_name(key, names)}; mark it shared "
                    "to let both use it"
                )
            if computer not in entry.get("shared", []):
                raise PlacementError(
                    f"{label} is assigned to {_name(key, names)} without sharing; mark it "
                    "shared there first"
                )
    return {"strategy": "manual", "computers": chosen, "shared": marked}


def _ordered(order: Iterable[Any], known: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Each known computer's ID: the saved order first, then the rest as the registry lists them."""
    named = (item for item in order if isinstance(item, str) and item in known)
    first = list(dict.fromkeys(named))
    return first + [computer_id for computer_id in known if computer_id not in first]


def _joined(words: list[str]) -> str:
    """Words in a sentence: "A", "A and B", "A, B and C"."""
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def notice(
    consumer: str, entry: Mapping[str, Any] | None, known: Mapping[str, Mapping[str, Any]]
) -> str:
    """A warning for a placement that leaves its work nowhere to start, or "" when it's fine.

    Work starts only on this computer for now (``placement_eligibility.not_yet``): a
    paired desktop takes a waiting research or conversation task only when the user
    picks Run there, and an organization's ticket only through a fleet execution target
    there. So a manual placement without this computer saves, but its work waits.
    """
    if not entry or entry.get("strategy") != "manual":
        return ""
    computers = list(entry.get("computers") or [])
    if any(known.get(c, {}).get("this_computer") for c in computers):
        return ""
    paired = [str(known[c].get("name") or "Desktop") for c in computers if c in known]
    picked = f"only {_joined(paired)}" if paired else "only unpaired computers"
    if consumer == REM:
        return (
            f"Rem's work starts only on this computer for now, so with {picked} picked, every "
            "task waits. A waiting research or conversation task can still go to "
            f"{_joined(paired) if paired else 'a paired desktop'} when you pick Run there."
        )
    where = "there" if paired else "on a paired desktop"
    return (
        f"Organization workers start only on this computer for now, so with {picked} picked, "
        f"its tickets wait unless they run through a fleet execution target {where}."
    )


def _view(
    entries: Mapping[str, Mapping[str, Any]],
    known: Mapping[str, Mapping[str, Any]],
    names: Mapping[str, str],
    order: Iterable[Any] = (),
) -> dict[str, Any]:
    consumers = []
    for key in (REM, *names):
        entry = entries.get(key)
        computers = list(entry["computers"]) if entry else []
        consumers.append(
            {
                "consumer": key,
                "kind": "rem" if key == REM else "organization",
                "name": _name(key, names),
                "strategy": entry["strategy"] if entry else DEFAULT_STRATEGY,
                # False until the user saves a placement for this consumer.
                "saved": entry is not None,
                "computers": computers,
                "shared": list(entry["shared"]) if entry else [],
                # Assigned computers no longer in the registry, such as an unpaired desktop.
                "missing_computers": [c for c in computers if c not in known],
                # Why this placement's work waits everywhere, or "" when it can start.
                "notice": notice(key, entry, known),
            }
        )
    return {
        "strategies": list(STRATEGIES),
        "default_strategy": DEFAULT_STRATEGY,
        "consumers": consumers,
        # The user's order of computers, which overflow fills in turn. Computers are
        # listed in it, so a computer paired since the last save comes last.
        "order": _ordered(order, known),
        "computers": [
            {
                "id": computer_id,
                "name": known[computer_id].get("name"),
                "state": known[computer_id].get("state"),
                "this_computer": bool(known[computer_id].get("this_computer")),
            }
            for computer_id in _ordered(order, known)
        ],
    }


def stored(data_dir: Path) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """The saved order of computers and each readable entry, for choosing a computer.

    A consumer with no entry here uses ``DEFAULT_STRATEGY`` with no assigned computers.
    """
    with registry._lock:
        order, entries = _placement(registry._load(data_dir))
    return [item for item in order if isinstance(item, str) and item], entries


def read(
    data_dir: Path,
    *,
    known_computers: Iterable[Mapping[str, Any]],
    organizations: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Rem's and each organization's placement, with the computers they can use."""
    with registry._lock:
        order, entries = _placement(registry._load(data_dir))
    return _view(entries, _computers(known_computers), _organizations(organizations), order)


def save(
    data_dir: Path,
    consumer: Any,
    strategy: Any,
    computers: Any = None,
    shared: Any = None,
    *,
    known_computers: Iterable[Mapping[str, Any]],
    organizations: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate and save one consumer's placement, then return every placement."""
    known = list(known_computers)
    orgs = list(organizations)
    names = _organizations(orgs)
    with registry._lock:
        store = registry._load(data_dir)
        order, entries = _placement(store)
        entry = validate(
            consumer,
            strategy,
            computers,
            shared,
            known_computers=known,
            organizations=orgs,
            others=entries,
        )
        entries[str(consumer).strip()] = entry
        store["placement"] = {
            "order": order,
            # A deleted organization's entry goes with this save.
            "consumers": {k: v for k, v in entries.items() if k == REM or k in names},
        }
        registry._save(data_dir, store)
    return _view(entries, _computers(known), names, order)


def validate_order(order: Any, *, known_computers: Iterable[Mapping[str, Any]]) -> list[str]:
    """Check the user's order of computers and return it as it would be saved.

    It names computers from the registry, each once; one it leaves out keeps its
    place after the named ones.
    """
    if order is None:
        raise PlacementError("Send order: computer IDs, first to fill first")
    chosen = _ids(order, "order")
    if not chosen:
        raise PlacementError("order needs at least one computer ID")
    known = _computers(known_computers)
    unknown = [c for c in chosen if c not in known]
    if unknown:
        raise PlacementError(f"No computer with the ID {unknown[0]}")
    return chosen


def save_order(
    data_dir: Path,
    order: Any,
    *,
    known_computers: Iterable[Mapping[str, Any]],
    organizations: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate and save the user's order of computers, then return every placement."""
    known = list(known_computers)
    names = _organizations(organizations)
    chosen = validate_order(order, known_computers=known)
    with registry._lock:
        store = registry._load(data_dir)
        _, entries = _placement(store)
        store["placement"] = {
            "order": chosen,
            "consumers": {k: v for k, v in entries.items() if k == REM or k in names},
        }
        registry._save(data_dir, store)
    return _view(entries, _computers(known), names, chosen)
