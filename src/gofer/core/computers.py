"""The computers Raticode can run work on, with each one's worker limit and load.

This computer is always listed. Each desktop paired with it is listed too, online
while its fleet status is fresh. A computer's ``max_threads`` is its worker limit:
on this computer it is Settings > Computers > Worker limit (``maxWorkers`` in the
command slot settings), where 0 picks a number from the cores and memory. A paired
desktop reports its own limit, running workers and pressure with its fleet status
(``load_reading``); an older desktop that doesn't shows them as unknown.

Each paired desktop also carries ``catalog``, the projects (each with its repository
fingerprint) and providers it shares with this computer, or None until it answers,
and ``work_reason``: why no work from this computer can run there whatever the work,
or None when only each start's own checks are left (gofer.core.placement_eligibility).

The load reading compares running workers with ``max_threads``. CPU and memory
pressure (from 0 to 100: Linux PSI stalls, the macOS memory pressure level and load
average, Windows busy time and memory load) only lower the number of workers that
fit; they never raise it above ``max_threads``.
A computer that is offline has no room. One that comes back has room for one new
worker until it has been back for ``REJOIN_SECONDS``, so its whole limit doesn't
fill at once.

A computer short of memory (gofer.core.command_slots.memory_hold) with a worker
already running has no room either: its ``memory_hold`` is the detail of why, such as
"0.9 GB available", and its ``capacity`` is 0. Paired desktops share the hold in their
load reading, so a held desktop takes none of this computer's work; an older desktop
that doesn't send it reads as not held.

This computer's reading lists ``running_work``: what each running worker is, as
``{kind, label}`` from the worker claims, at most ``RUNNING_WORK_LIMIT``. Paired desktops
share only ``running_by_kind``, the counts by kind and never titles; an older desktop
that doesn't send them shows only its total (``running_by_kind`` None).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from gofer.core import command_slots
from gofer.core.command_slots import (
    MAX_WORKERS,
    SlotSettings,
    cpu_count,
    default_limits,
    memory_pressure,
    read_settings,
    slots_root,
    write_settings,
)
from gofer.core.placement_eligibility import desktop_reason
from gofer.core.worker_claims import CONSUMERS, RUNNING_WORK_LIMIT, clean_label

# The longest memory hold detail a paired desktop's reading may carry.
MEMORY_HOLD_CHARS = 80
# Below this much pressure a computer takes its full limit; at FULL it takes one worker.
PRESSURE_FREE = 20.0
PRESSURE_FULL = 80.0
# How long a computer that came back online is held to one new worker.
REJOIN_SECONDS = 60
_STORE = "computers.json"
_lock = threading.Lock()


class ComputerError(ValueError):
    """A change the registry refuses, with the reason in words."""


# Load math -------------------------------------------------------------------------


def _pressure(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(100.0, float(value)))


def effective_threads(
    max_threads: int, cpu_pressure: float | None = None, memory_pressure: float | None = None
) -> int:
    """How many workers fit right now: ``max_threads``, lowered by pressure.

    The higher of the two pressures counts. At or below ``PRESSURE_FREE`` nothing
    changes; from there to ``PRESSURE_FULL`` the limit falls in a straight line to
    one worker. The result is never above ``max_threads`` and, for a limit of at
    least one, never below one, as one worker can always start.
    """
    if max_threads <= 0:
        return 0
    worst = max(
        (p for p in (_pressure(cpu_pressure), _pressure(memory_pressure)) if p is not None),
        default=0.0,
    )
    if worst <= PRESSURE_FREE:
        return max_threads
    share = min(1.0, (worst - PRESSURE_FREE) / (PRESSURE_FULL - PRESSURE_FREE))
    return max(1, min(max_threads, int(max_threads * (1 - share))))


def capacity(
    *,
    online: bool,
    max_threads: int | None,
    running: int,
    cpu_pressure: float | None = None,
    memory_pressure: float | None = None,
    rejoining: bool = False,
) -> int | None:
    """New workers this computer can take now. None when its limit isn't known."""
    if not online:
        return 0
    if max_threads is None:
        return None
    free = max(0, effective_threads(max_threads, cpu_pressure, memory_pressure) - running)
    return min(free, 1) if rejoining else free


# Readings from this computer -------------------------------------------------------


def _windows_system_times() -> tuple[int, int, int] | None:
    """Windows idle, kernel and user CPU time so far (kernel includes idle), or None."""
    try:
        import ctypes
        from ctypes import wintypes

        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(  # type: ignore[attr-defined]
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        ):
            return None
        return tuple(  # type: ignore[return-value]
            (part.dwHighDateTime << 32) | part.dwLowDateTime for part in (idle, kernel, user)
        )
    except Exception:  # noqa: BLE001 - any failure means no reading
        return None


# The Windows CPU reading needs two samples at least this far apart.
CPU_SAMPLE_SECONDS = 1.0
_cpu_lock = threading.Lock()
# The last Windows sample (monotonic time, idle, kernel, user) and the pressure it gave.
_cpu_sample: tuple[float, int, int, int] | None = None
_cpu_last: float | None = None
_monotonic = time.monotonic


def _windows_cpu_pressure() -> float | None:
    """CPU pressure on Windows from the change in GetSystemTimes since the last read.

    The busy share is 1 - idle / (kernel + user) over that change, made into pressure
    with ``command_slots.busy_pressure`` (80% busy or less is 0, 100% is 100). The first
    read only takes a sample and returns None; a read less than ``CPU_SAMPLE_SECONDS``
    after the sample returns the last pressure without a new sample.
    """
    global _cpu_sample, _cpu_last
    times = _windows_system_times()
    if times is None:
        return None
    now = _monotonic()
    with _cpu_lock:
        previous = _cpu_sample
        if previous is not None and now - previous[0] < CPU_SAMPLE_SECONDS:
            return _cpu_last
        _cpu_sample = (now, *times)
        if previous is None:
            return None
        idle, kernel, user = (new - old for new, old in zip(times, previous[1:], strict=True))
        total = kernel + user
        if total <= 0 or idle < 0:
            return _cpu_last
        busy = max(0.0, min(1.0, 1 - idle / total))
        _cpu_last = command_slots.busy_pressure(busy * 100)
        return _cpu_last


def cpu_pressure() -> float | None:
    """CPU pressure from 0 to 100, or None without a reading.

    Linux reports the share of the last ten seconds tasks waited for a CPU (PSI).
    Windows reads busy time from GetSystemTimes (``_windows_cpu_pressure``). Elsewhere,
    such as macOS, it is the run queue beyond the core count from the one-minute load
    average, or None without one.
    """
    if sys.platform == "win32":
        return _windows_cpu_pressure()
    try:
        with open("/proc/pressure/cpu", encoding="ascii") as pressure:
            for line in pressure:
                if line.startswith("some "):
                    fields = dict(item.split("=", 1) for item in line.split()[1:])
                    return _pressure(float(fields["avg10"]))
    except (OSError, ValueError, KeyError):
        pass
    try:
        load = os.getloadavg()[0]
    except (AttributeError, OSError):
        return None
    cores = cpu_count()
    return _pressure((load - cores) / cores * 100)


def local_pressure() -> tuple[float | None, float | None]:
    return cpu_pressure(), _pressure(memory_pressure())


def local_memory_hold(running: int, memory: Callable[[], str | None] | None = None) -> str | None:
    """This computer's memory hold detail while a worker runs here, or None.

    ``memory`` reads the hold (``command_slots.memory_hold`` when None, looked up when
    called), so the first worker on a computer always starts.
    """
    if running <= 0:
        return None
    return memory() if memory is not None else command_slots.memory_hold()


def local_room_limit(
    data_dir: Path,
    pressure: Callable[[], tuple[float | None, float | None]] | None = None,
) -> int:
    """This computer's worker limit lowered by CPU and memory pressure (``effective_threads``).

    Starts that claim a worker without placement (swarm turns, work another desktop's
    organization sent) count against this, as placed starts do through ``capacity``.
    ``pressure`` defaults to ``local_pressure``, looked up when called.
    """
    limit = int(read_settings(slots_root(data_dir)).resolved()["maxWorkers"])
    return effective_threads(limit, *(pressure or local_pressure)())


# The store ---------------------------------------------------------------------------


def _store_path(data_dir: Path) -> Path:
    return data_dir / _STORE


def _load(data_dir: Path) -> dict[str, Any]:
    try:
        value = json.loads(_store_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _save(data_dir: Path, value: dict[str, Any]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = _store_path(data_dir)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def this_computer_id(data_dir: Path, device_id: str | None = None) -> str:
    """This computer's stable ID: its paired-device ID, or one kept in the data folder."""
    if device_id:
        return device_id
    with _lock:
        store = _load(data_dir)
        known = store.get("this_id")
        if isinstance(known, str) and known:
            return known
        store["this_id"] = str(uuid.uuid4())
        _save(data_dir, store)
        return str(store["this_id"])


# The worker limit ----------------------------------------------------------------------


def set_max_threads(data_dir: Path, value: Any) -> SlotSettings:
    """Save this computer's worker limit from the API, the CLI or the phone. 0 is automatic.

    The desktop window also sends this limit with Settings > Rem. The value set here
    is kept until the window sends a different number of its own, so a window that
    hasn't caught up doesn't undo it.
    """
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_WORKERS:
        raise ComputerError(f"max_threads must be a whole number from 0 to {MAX_WORKERS}")
    root = slots_root(data_dir)
    with _lock:
        current = read_settings(root)
        store = _load(data_dir)
        window = store.get("window_max_threads")
        store["external_max_threads"] = {
            "value": value,
            "window_value": window if isinstance(window, int) else current.max_workers,
        }
        updated = replace(current, max_workers=value)
        write_settings(root, updated)
        _save(data_dir, store)
    return updated


def window_settings(data_dir: Path, slots: SlotSettings) -> SlotSettings:
    """The window's command slot settings, keeping a limit set elsewhere since its last change."""
    with _lock:
        store = _load(data_dir)
        external = store.get("external_max_threads")
        kept = slots
        if (
            isinstance(external, dict)
            and external.get("window_value") == slots.max_workers
            and isinstance(external.get("value"), int)
        ):
            kept = replace(slots, max_workers=int(external["value"]))
        elif external is not None:
            store.pop("external_max_threads", None)
        if store.get("window_max_threads") != slots.max_workers or external is not None:
            store["window_max_threads"] = slots.max_workers
            _save(data_dir, store)
        return kept


# The registry --------------------------------------------------------------------------


def _track(store: dict[str, Any], computer_id: str, online: bool, now: float) -> tuple[bool, bool]:
    """Record a computer's state; returns (rejoining, changed)."""
    seen = store.setdefault("seen", {})
    state = "online" if online else "offline"
    entry = seen.get(computer_id)
    changed = not isinstance(entry, dict) or entry.get("state") != state
    if changed:
        # A computer seen for the first time hasn't come back from anything.
        rejoined = isinstance(entry, dict) and online
        seen[computer_id] = {"state": state, "since": now, "rejoined": rejoined}
        entry = seen[computer_id]
    rejoining = bool(entry.get("rejoined")) and now - float(entry.get("since", 0)) < REJOIN_SECONDS
    return rejoining, changed


def _peer_load(value: Any) -> dict[str, Any] | None:
    """A paired desktop's reported reading, or None when it sent none or a bad one."""
    if not isinstance(value, Mapping):
        return None
    limit, running = value.get("max_threads"), value.get("running_threads")
    if type(limit) is not int or type(running) is not int or limit < 1 or running < 0:
        return None
    pressures = []
    for key in ("cpu_pressure", "memory_pressure"):
        item = value.get(key)
        ok = item is None or (isinstance(item, int | float) and not isinstance(item, bool))
        pressures.append(_pressure(item) if ok else None)
    return {
        "max_threads": min(limit, MAX_WORKERS),
        "running_threads": running,
        "cpu_pressure": pressures[0],
        "memory_pressure": pressures[1],
        "memory_hold": _peer_memory_hold(value.get("memory_hold")),
        "running_by_kind": _peer_kinds(value.get("running_by_kind"), running),
    }


def _peer_kinds(value: Any, running: int) -> dict[str, int] | None:
    """A paired desktop's running workers by kind, or None (an older desktop, or a bad one).

    Every kind is listed, 0 when it has none. Counts that don't add up to its running
    total are dropped, so a row never shows a breakdown that disagrees with its total.
    """
    if not isinstance(value, Mapping) or set(value) - set(CONSUMERS):
        return None
    counts = dict.fromkeys(CONSUMERS, 0)
    for kind, count in value.items():
        if type(count) is not int or count < 0:
            return None
        counts[kind] = count
    return counts if sum(counts.values()) == running else None


def _running_work(items: Iterable[Any]) -> list[dict[str, str]]:
    """This computer's workers as its reading lists them: known kinds, short labels, capped."""
    work = []
    for item in items:
        if not isinstance(item, Mapping) or item.get("kind") not in CONSUMERS:
            continue
        work.append({"kind": str(item["kind"]), "label": clean_label(item.get("label"), "worker")})
        if len(work) >= RUNNING_WORK_LIMIT:
            break
    return work


def _peer_memory_hold(value: Any) -> str | None:
    """A paired desktop's memory hold detail, or None (not held, or an older desktop).

    It is shown on waiting work, so only short printable text is kept.
    """
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())[:MEMORY_HOLD_CHARS]
    if not text or not text.isprintable():
        return None
    return text


def load_reading(
    data_dir: Path,
    running: Mapping[str, int],
    pressure: Callable[[], tuple[float | None, float | None]] = local_pressure,
    memory: Callable[[], str | None] | None = None,
) -> dict[str, Any]:
    """This computer's reading as it shares it with paired desktops.

    ``memory_hold`` is set while this computer is short of memory with a worker
    running, so paired desktops place none of their work here meanwhile.
    ``running_by_kind`` counts the workers by kind, never what they are.
    """
    cpu, pressure_memory = pressure()
    kinds = {kind: min(1000, max(0, int(running.get(kind, 0)))) for kind in CONSUMERS}
    total = min(1000, sum(max(0, int(v)) for v in running.values()))
    if sum(kinds.values()) != total:
        # Kinds outside CONSUMERS (none yet), or a total past the cap: share the total only.
        kinds = {}
    return {
        "max_threads": read_settings(slots_root(data_dir)).resolved()["maxWorkers"],
        "running_threads": total,
        "cpu_pressure": None if cpu is None else round(cpu, 2),
        "memory_pressure": None if pressure_memory is None else round(pressure_memory, 2),
        "memory_hold": local_memory_hold(total, memory),
        **({"running_by_kind": kinds} if kinds else {}),
    }


def _peer_catalog(value: Any) -> dict[str, Any] | None:
    """A paired desktop's catalog for placement, or None when it hasn't answered."""
    if not isinstance(value, Mapping) or value.get("state") not in (
        "shared",
        "unsupported",
        "refused",
    ):
        return None
    if value["state"] != "shared":
        return {"state": value["state"]}
    projects = [
        {key: p[key] for key in ("id", "name", "fingerprint") if key in p}
        for p in value.get("projects") or []
        if isinstance(p, Mapping) and isinstance(p.get("id"), str)
    ]
    providers = [str(p) for p in value.get("providers") or [] if isinstance(p, str)]
    return {"state": "shared", "projects": projects, "providers": providers}


def _reading(
    *,
    computer_id: str,
    name: str,
    this_computer: bool,
    online: bool,
    max_threads: int | None,
    running: int,
    cpu: float | None,
    memory: float | None,
    rejoining: bool,
    now: float,
    last_seen: float | None,
    memory_hold: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    held = memory_hold if online else None
    room = capacity(
        online=online,
        max_threads=max_threads,
        running=running,
        cpu_pressure=cpu,
        memory_pressure=memory,
        rejoining=rejoining,
    )
    return {
        "id": computer_id,
        "name": name,
        "this_computer": this_computer,
        "state": "online" if online else "offline",
        "max_threads": max_threads,
        "running_threads": running if online else 0,
        "cpu_pressure": cpu if online else None,
        "memory_pressure": memory if online else None,
        "effective_threads": (
            effective_threads(max_threads, cpu, memory)
            if online and max_threads is not None
            else (0 if not online else None)
        ),
        # A computer short of memory takes no new worker until memory frees up.
        "capacity": 0 if held and room is not None else room,
        "memory_hold": held,
        "rejoining": rejoining and online,
        "checked_at": int(now),
        "last_seen": int(last_seen) if last_seen else None,
        **extra,
    }


def computers(
    data_dir: Path,
    *,
    name: str,
    device_id: str | None = None,
    running: Mapping[str, int] | None = None,
    fleet: Iterable[Mapping[str, Any]] = (),
    pressure: Callable[[], tuple[float | None, float | None]] = local_pressure,
    clock: Callable[[], float] = time.time,
    memory: Callable[[], str | None] | None = None,
    running_work: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """This computer and each paired desktop, with its state, worker limit and load.

    ``running`` counts this computer's workers by kind (Rem tasks, phone turns,
    organization workers, swarm turns) and ``running_work`` says what each one is
    (``{kind, label}``). ``fleet`` is the device service's fleet list. ``memory``
    reads this computer's memory hold (see ``local_memory_hold``).
    """
    now = clock()
    settings = read_settings(slots_root(data_dir))
    counts = {key: max(0, int(value)) for key, value in (running or {}).items()}
    cpu, memory_pressure_now = pressure()
    local_id = this_computer_id(data_dir, device_id)
    with _lock:
        store = _load(data_dir)
        changed = False
        _, local_changed = _track(store, local_id, True, now)
        changed |= local_changed
        items = [
            _reading(
                computer_id=local_id,
                name=name,
                this_computer=True,
                online=True,
                max_threads=settings.resolved()["maxWorkers"],
                running=sum(counts.values()),
                cpu=cpu,
                memory=memory_pressure_now,
                rejoining=False,
                now=now,
                last_seen=now,
                memory_hold=local_memory_hold(sum(counts.values()), memory),
                max_threads_setting=settings.max_workers,
                default_max_threads=default_limits()["maxWorkers"],
                running_by_kind=counts,
                running_work=_running_work(running_work),
                # Only paired desktops share a catalog; this computer runs its own work.
                catalog=None,
                work_reason=None,
            )
        ]
        for peer in fleet:
            if peer.get("role") != "desktop" or peer.get("state") != "active":
                continue
            peer_id = str(peer.get("device_id") or "")
            if not peer_id or peer_id == local_id:
                continue
            online = peer.get("reachability") == "reachable"
            rejoining, peer_changed = _track(store, peer_id, online, now)
            changed |= peer_changed
            jobs = peer.get("running_jobs")
            # A desktop that reports its reading gives its own limit, workers and pressure.
            load = _peer_load(peer.get("load")) if online else None
            items.append(
                _reading(
                    computer_id=peer_id,
                    name=str(peer.get("name") or "Desktop"),
                    this_computer=False,
                    online=online,
                    max_threads=load["max_threads"] if load else None,
                    running=(
                        load["running_threads"]
                        if load
                        else len(jobs)
                        if isinstance(jobs, list)
                        else 0
                    ),
                    cpu=load["cpu_pressure"] if load else None,
                    memory=load["memory_pressure"] if load else None,
                    rejoining=rejoining,
                    now=now,
                    last_seen=peer.get("last_seen"),
                    memory_hold=load["memory_hold"] if load else None,
                    max_threads_setting=None,
                    default_max_threads=None,
                    # Counts only: a paired desktop never shares what its workers are.
                    running_by_kind=load["running_by_kind"] if load else None,
                    running_work=None,
                    catalog=_peer_catalog(peer.get("catalog")),
                    work_reason=None,
                )
            )
            items[-1]["work_reason"] = desktop_reason(items[-1])
        if changed:
            _save(data_dir, store)
    return {"computers": items, "checked_at": int(now), "rejoin_seconds": REJOIN_SECONDS}


class ComputerService:
    """The registry as the backend serves it to the API, the CLI and the phone."""

    def __init__(
        self,
        data_dir: Path,
        *,
        name: Callable[[], str],
        device_id: Callable[[], str | None] = lambda: None,
        running: Callable[[], Mapping[str, int]] = dict,
        fleet: Callable[[], Iterable[Mapping[str, Any]]] = list,
        pressure: Callable[[], tuple[float | None, float | None]] = local_pressure,
        clock: Callable[[], float] = time.time,
        organizations: Callable[[], Iterable[Mapping[str, Any]]] = list,
        memory: Callable[[], str | None] | None = None,
        running_work: Callable[[], Iterable[Mapping[str, Any]]] = list,
    ) -> None:
        self.data_dir = data_dir
        self.name = name
        self.device_id = device_id
        self.running = running
        # What each of this computer's workers is ({kind, label}), for its own reading only.
        self.running_work = running_work
        self.fleet = fleet
        # Each organization's id and name, for placement settings.
        self.organizations = organizations
        self.pressure = pressure
        self.clock = clock
        # Why this computer is short of memory, or None (command_slots.memory_hold).
        self.memory = memory

    def read(self) -> dict[str, Any]:
        try:
            fleet = list(self.fleet())
        except Exception:
            # Paired desktops are optional; this computer is always listed.
            fleet = []
        return computers(
            self.data_dir,
            name=self.name(),
            device_id=self.device_id(),
            running=self.running(),
            fleet=fleet,
            pressure=self.pressure,
            clock=self.clock,
            memory=self.memory,
            running_work=self.running_work(),
        )

    def load_reading(self) -> dict[str, Any]:
        """This computer's limit, running workers and pressure, for paired desktops.

        Called while the device service holds its own locks, so the running counts
        it reads take no locks of theirs.
        """
        return load_reading(self.data_dir, self.running(), self.pressure, self.memory)

    def set_max_threads(self, computer_id: Any, max_threads: Any) -> dict[str, Any]:
        """Change this computer's worker limit; a paired desktop's is changed on it."""
        view = self.read()
        if computer_id not in (None, "", "this"):
            match = next((c for c in view["computers"] if c["id"] == computer_id), None)
            if match is None:
                raise ComputerError("No computer with that ID")
            if not match["this_computer"]:
                raise ComputerError(
                    f"Change {match['name']}'s worker limit in Raticode on that computer"
                )
        set_max_threads(self.data_dir, max_threads)
        return self.read()

    def placement(self) -> dict[str, Any]:
        """Rem's and each organization's placement strategy and assigned computers."""
        from gofer.core import placement_settings

        return placement_settings.read(
            self.data_dir,
            known_computers=self.read()["computers"],
            organizations=list(self.organizations()),
        )

    def set_placement(
        self, consumer: Any, strategy: Any, computers: Any = None, shared: Any = None
    ) -> dict[str, Any]:
        """Validate and save one consumer's placement, then return every placement."""
        from gofer.core import placement_settings

        return placement_settings.save(
            self.data_dir,
            consumer,
            strategy,
            computers,
            shared,
            known_computers=self.read()["computers"],
            organizations=list(self.organizations()),
        )

    def set_order(self, order: Any) -> dict[str, Any]:
        """Save the user's order of computers, which overflow fills in turn; returns placements."""
        from gofer.core import placement_settings

        return placement_settings.save_order(
            self.data_dir,
            order,
            known_computers=self.read()["computers"],
            organizations=list(self.organizations()),
        )
