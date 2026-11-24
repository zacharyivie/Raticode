"""The computer registry: worker limits, load readings, offline computers and rejoining."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from gofer.core import computers as module
from gofer.core.command_slots import SlotSettings, read_settings, slots_root, write_settings
from gofer.core.computers import (
    ComputerError,
    ComputerService,
    capacity,
    effective_threads,
    set_max_threads,
    window_settings,
)
from tests.unit.test_device_application import application as application
from tests.unit.test_device_desktop_control import phone as phone

PEER = "00000000-0000-4000-8000-0000000000aa"


class Clock:
    def __init__(self) -> None:
        self.now = 1_900_000_000.0

    def __call__(self) -> float:
        return self.now


def desktop(online: bool, jobs: int = 0, peer: str = PEER) -> dict[str, Any]:
    return {
        "device_id": peer,
        "name": "Laptop",
        "role": "desktop",
        "state": "active",
        "reachability": "reachable" if online else "unknown",
        "last_seen": 1_899_999_000,
        "running_jobs": [f"00000000-0000-4000-8000-{n:012d}" for n in range(jobs)],
    }


def service(
    tmp_path: Path,
    *,
    fleet: list[dict[str, Any]] | None = None,
    running: dict[str, int] | None = None,
    pressure: tuple[float | None, float | None] = (None, None),
    clock: Clock | None = None,
) -> ComputerService:
    return ComputerService(
        tmp_path,
        name=lambda: "Studio",
        device_id=lambda: "00000000-0000-4000-8000-000000000001",
        running=lambda: running or {},
        fleet=lambda: fleet or [],
        pressure=lambda: pressure,
        clock=clock or Clock(),
    )


def by_id(view: dict[str, Any], identifier: str) -> dict[str, Any]:
    return next(c for c in view["computers"] if c["id"] == identifier)


# Capacity math ---------------------------------------------------------------------


def test_capacity_is_the_limit_minus_running_workers_and_never_negative() -> None:
    assert capacity(online=True, max_threads=4, running=0) == 4
    assert capacity(online=True, max_threads=4, running=3) == 1
    assert capacity(online=True, max_threads=4, running=6) == 0
    assert capacity(online=True, max_threads=None, running=0) is None


def test_pressure_only_lowers_capacity_and_never_raises_it_above_the_limit() -> None:
    assert effective_threads(8) == 8
    # Calm or missing readings change nothing; readings outside 0..100 are clamped.
    assert effective_threads(8, 10.0, None) == 8
    assert effective_threads(8, -50.0, -1.0) == 8
    assert effective_threads(8, None, module.PRESSURE_FREE) == 8
    # Halfway between free and full pressure halves the limit; the worse reading counts.
    halfway = (module.PRESSURE_FREE + module.PRESSURE_FULL) / 2
    assert effective_threads(8, halfway, 0.0) == 4
    assert effective_threads(8, 0.0, halfway) == 4
    # Full pressure leaves one worker, as one can always start.
    assert effective_threads(8, module.PRESSURE_FULL, None) == 1
    assert effective_threads(8, 500.0, None) == 1
    for pressure in range(0, 101, 5):
        assert 1 <= effective_threads(6, float(pressure), float(pressure)) <= 6
    assert capacity(online=True, max_threads=8, running=3, cpu_pressure=halfway) == 1
    assert capacity(online=True, max_threads=8, running=5, memory_pressure=halfway) == 0


def test_this_computers_reading_uses_its_worker_limit_running_workers_and_pressure(
    tmp_path: Path,
) -> None:
    write_settings(slots_root(tmp_path), SlotSettings(max_workers=6))
    halfway = (module.PRESSURE_FREE + module.PRESSURE_FULL) / 2
    view = service(
        tmp_path,
        running={"rem": 1, "phone": 1, "organizations": 0, "swarms": 0},
        pressure=(halfway, 3.0),
    ).read()
    [this] = view["computers"]
    assert this["this_computer"] is True and this["name"] == "Studio"
    assert this["id"] == "00000000-0000-4000-8000-000000000001"
    assert this["state"] == "online" and this["max_threads"] == 6
    assert this["max_threads_setting"] == 6 and this["running_threads"] == 2
    assert this["running_by_kind"] == {"rem": 1, "phone": 1, "organizations": 0, "swarms": 0}
    assert this["cpu_pressure"] == halfway and this["memory_pressure"] == 3.0
    assert this["effective_threads"] == 3 and this["capacity"] == 1


def test_automatic_limit_comes_from_the_existing_defaults(tmp_path: Path) -> None:
    [this] = service(tmp_path).read()["computers"]
    assert this["max_threads_setting"] == 0
    assert this["max_threads"] == this["default_max_threads"] >= 1


def test_without_a_paired_device_id_this_computer_keeps_one_stable_id(tmp_path: Path) -> None:
    def plain() -> ComputerService:
        return ComputerService(tmp_path, name=lambda: "Studio", pressure=lambda: (None, None))

    first = plain().read()["computers"][0]["id"]
    assert first and plain().read()["computers"][0]["id"] == first


# Offline and rejoining ---------------------------------------------------------------


def test_offline_computer_has_no_room_and_rejoins_without_a_burst(tmp_path: Path) -> None:
    clock = Clock()
    fleet = [desktop(True, jobs=1)]
    registry = service(tmp_path, fleet=fleet, clock=clock)
    first = by_id(registry.read(), PEER)
    # A paired desktop doesn't report its limit yet, so its room isn't known.
    assert first["state"] == "online" and first["running_threads"] == 1
    assert first["max_threads"] is None and first["capacity"] is None
    assert first["rejoining"] is False

    fleet[0] = desktop(False, jobs=1)
    clock.now += 30
    gone = by_id(registry.read(), PEER)
    assert gone["state"] == "offline" and gone["capacity"] == 0
    assert gone["running_threads"] == 0 and gone["effective_threads"] == 0

    # Back on the next check, held to one new worker for REJOIN_SECONDS.
    fleet[0] = desktop(True)
    clock.now += 30
    back = by_id(registry.read(), PEER)
    assert back["state"] == "online" and back["rejoining"] is True
    clock.now += module.REJOIN_SECONDS
    assert by_id(registry.read(), PEER)["rejoining"] is False


def test_a_rejoining_computer_with_a_known_limit_gets_one_worker_first() -> None:
    assert capacity(online=True, max_threads=8, running=0, rejoining=True) == 1
    assert capacity(online=True, max_threads=8, running=8, rejoining=True) == 0
    assert capacity(online=False, max_threads=8, running=0) == 0


def test_revoked_phones_and_this_computer_are_not_listed_as_paired_desktops(
    tmp_path: Path,
) -> None:
    fleet = [
        desktop(True),
        {**desktop(True, peer="00000000-0000-4000-8000-0000000000bb"), "role": "controller"},
        {**desktop(True, peer="00000000-0000-4000-8000-0000000000cc"), "state": "revoked"},
        desktop(True, peer="00000000-0000-4000-8000-000000000001"),
    ]
    ids = [c["id"] for c in service(tmp_path, fleet=fleet).read()["computers"]]
    assert ids == ["00000000-0000-4000-8000-000000000001", PEER]


def test_a_failing_fleet_still_lists_this_computer(tmp_path: Path) -> None:
    def broken() -> list[dict[str, Any]]:
        raise RuntimeError("device storage locked")

    registry = ComputerService(tmp_path, name=lambda: "Studio", fleet=broken)
    assert [c["this_computer"] for c in registry.read()["computers"]] == [True]


# Changing the limit ----------------------------------------------------------------


def test_set_max_threads_saves_the_worker_limit_and_refuses_bad_values(tmp_path: Path) -> None:
    registry = service(tmp_path, fleet=[desktop(True)])
    view = registry.set_max_threads(None, 5)
    assert view["computers"][0]["max_threads"] == 5
    assert read_settings(slots_root(tmp_path)).max_workers == 5
    assert registry.set_max_threads("this", 0)["computers"][0]["max_threads_setting"] == 0
    for bad in (-1, 17, True, "4", 2.5):
        with pytest.raises(ComputerError):
            registry.set_max_threads(None, bad)
    with pytest.raises(ComputerError, match="on that computer"):
        registry.set_max_threads(PEER, 3)
    with pytest.raises(ComputerError, match="No computer"):
        registry.set_max_threads("missing", 3)


def test_a_limit_set_elsewhere_stands_until_the_window_sends_its_own(tmp_path: Path) -> None:
    window = SlotSettings(max_workers=4, test=2)
    assert window_settings(tmp_path, window).max_workers == 4
    set_max_threads(tmp_path, 7)
    # The window resends its old number: the CLI's or phone's 7 stands.
    kept = window_settings(tmp_path, window)
    assert kept.max_workers == 7 and kept.test == 2
    # The user picks a new number in the window: it wins.
    assert window_settings(tmp_path, SlotSettings(max_workers=3)).max_workers == 3
    assert window_settings(tmp_path, SlotSettings(max_workers=4)).max_workers == 4


# API, CLI and phone ------------------------------------------------------------------


def test_each_computer_shows_what_its_running_workers_are(tmp_path: Path, monkeypatch) -> None:
    from gofer.core.worker_claims import WorkerClaims
    from tests.unit import test_ui_server as ui_tests

    write_settings(slots_root(tmp_path), SlotSettings(max_workers=4))
    claims = WorkerClaims(memory=lambda: None)
    here = "00000000-0000-4000-8000-000000000001"
    claims.claim(here, "organizations", 4, label="Acme · Ada")
    claims.claim(here, "rem", 4, label="Fix the login page")
    # An organization worker is named once its ticket is picked.
    claims.claim(here, "organizations", 4).describe("Acme · Grace")  # type: ignore[union-attr]
    claims.claim(here, "swarms", 4, label="Release team")
    paired = {
        **desktop(True),
        "load": {
            "max_threads": 8,
            "running_threads": 3,
            "cpu_pressure": None,
            "memory_pressure": None,
            "running_by_kind": {"rem": 1, "phone": 0, "organizations": 2, "swarms": 0},
        },
    }
    older = {
        **desktop(True, peer="00000000-0000-4000-8000-0000000000bb"),
        "load": {
            "max_threads": 2,
            "running_threads": 1,
            "cpu_pressure": None,
            "memory_pressure": None,
        },
    }
    registry = ComputerService(
        tmp_path,
        name=lambda: "Studio",
        device_id=lambda: here,
        running=claims.running,
        running_work=claims.running_work,
        fleet=lambda: [paired, older],
        pressure=lambda: (None, None),
        clock=Clock(),
    )
    real = ui_tests._fake_server

    def with_computers(path: Path, **options: Any) -> Any:
        server = real(path, **options)
        server.computers = registry
        return server

    monkeypatch.setattr(ui_tests, "_fake_server", with_computers)
    listed = ui_tests._request(tmp_path, "GET", "/api/computers")
    assert listed.status == 200
    view = cast(dict[str, Any], listed.json())
    this = by_id(view, here)
    assert this["running_threads"] == 4 and this["capacity"] == 0
    assert this["running_by_kind"] == {"rem": 1, "phone": 0, "organizations": 2, "swarms": 1}
    assert this["running_work"] == [
        {"kind": "organizations", "label": "Acme · Ada"},
        {"kind": "rem", "label": "Fix the login page"},
        {"kind": "organizations", "label": "Acme · Grace"},
        {"kind": "swarms", "label": "Release team"},
    ]
    # A paired desktop shares its counts by kind, never what its workers are.
    desk = by_id(view, PEER)
    assert desk["running_by_kind"] == {"rem": 1, "phone": 0, "organizations": 2, "swarms": 0}
    assert desk["running_work"] is None
    # An older desktop that sends no counts shows only its total.
    old = by_id(view, older["device_id"])
    assert old["running_threads"] == 1 and old["running_by_kind"] is None


def test_running_work_is_capped_and_labels_are_short(tmp_path: Path) -> None:
    from gofer.core.worker_claims import LABEL_CHARS, RUNNING_WORK_LIMIT, WorkerClaims

    claims = WorkerClaims(memory=lambda: None)
    for n in range(RUNNING_WORK_LIMIT + 5):
        claims.record("this", "rem", f"Task {n}")
    claims.record("this", "phone", "x" * 500 + "\n\x1b[31m")
    claims.record("this", "phone")
    work = claims.running_work()
    assert len(work) == RUNNING_WORK_LIMIT and work[0] == {"kind": "rem", "label": "Task 0"}
    labels = [w["label"] for w in claims.running_work(limit=100)[-2:]]
    assert len(labels[0]) == LABEL_CHARS and labels[0].isprintable()
    # A worker taken without a label is named by its kind.
    assert labels[1] == "phone turn"
    view = ComputerService(
        tmp_path,
        name=lambda: "Studio",
        running=claims.running,
        running_work=lambda: claims.running_work(limit=100),
        pressure=lambda: (None, None),
    ).read()
    assert len(view["computers"][0]["running_work"]) == RUNNING_WORK_LIMIT


def test_a_paired_desktops_counts_by_kind_must_add_up_to_its_total(tmp_path: Path) -> None:
    load = {"max_threads": 8, "running_threads": 3, "cpu_pressure": None, "memory_pressure": None}

    def kinds(value: Any) -> Any:
        reported = {**desktop(True), "load": {**load, "running_by_kind": value}}
        return by_id(service(tmp_path, fleet=[reported]).read(), PEER)["running_by_kind"]

    assert kinds({"rem": 3}) == {"rem": 3, "phone": 0, "organizations": 0, "swarms": 0}
    assert kinds({"rem": 1}) is None
    assert kinds({"rem": 3, "builds": 0}) is None
    assert kinds({"rem": -1, "phone": 4}) is None
    assert kinds({"rem": True, "phone": 2}) is None
    assert kinds("3 Rem tasks") is None


def test_settings_api_lists_computers_and_changes_the_limit(tmp_path: Path, monkeypatch) -> None:
    from tests.unit import test_ui_server as ui_tests

    real = ui_tests._fake_server

    def with_computers(path: Path, **options: Any) -> Any:
        server = real(path, **options)
        server.computers = service(path)
        return server

    monkeypatch.setattr(ui_tests, "_fake_server", with_computers)
    assert ui_tests._request(tmp_path, "GET", "/api/computers", authenticated=False).status == 401
    listed = ui_tests._request(tmp_path, "GET", "/api/computers")
    assert listed.status == 200
    assert cast(dict[str, Any], listed.json())["computers"][0]["this_computer"] is True
    changed = ui_tests._request(tmp_path, "POST", "/api/computers", body={"max_threads": 3})
    assert changed.status == 200
    assert cast(dict[str, Any], changed.json())["computers"][0]["max_threads"] == 3
    bad = ui_tests._request(tmp_path, "POST", "/api/computers", body={"max_threads": 99})
    assert bad.status == 400
    extra = ui_tests._request(
        tmp_path, "POST", "/api/computers", body={"max_threads": 2, "path": "/etc"}
    )
    assert extra.status == 400


def test_phone_reads_computers_and_changes_this_computers_limit(phone, tmp_path: Path) -> None:
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    assert send("desktop.read", "overview")["value"]["computers"] is False
    assert send("desktop.read", "computers.list")["code"] == "unavailable"
    app.desktop.computers = service(tmp_path / "computers", fleet=[desktop(False)])
    assert send("desktop.read", "overview")["value"]["computers"] is True
    view = send("desktop.read", "computers.list")["value"]
    assert [c["state"] for c in view["computers"]] == ["online", "offline"]
    assert view["computers"][1]["capacity"] == 0
    changed = send("desktop.action", "computers.set", {"max_threads": 2})["value"]
    assert changed["computers"][0]["max_threads"] == 2
    refused = send("desktop.action", "computers.set", {"computer_id": PEER, "max_threads": 2})
    assert refused["code"] == "invalid"
    # The protocol schema refuses an out-of-range limit or an unknown field.
    assert send("desktop.action", "computers.set", {"max_threads": 40})["ok"] is False
    assert send("desktop.action", "computers.set", {"max_threads": 1, "path": "/"})["ok"] is False
    app.desktop.enable(peer, False)
    assert send("desktop.read", "computers.list")["code"] == "unauthorized"


def test_phone_reads_and_changes_where_work_runs(phone, tmp_path: Path) -> None:
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    assert send("desktop.read", "overview")["value"]["computer_placement"] is False
    assert send("desktop.read", "computers.placement")["code"] == "unavailable"
    app.desktop.computers = service(tmp_path / "computers", fleet=[desktop(True)])
    assert send("desktop.read", "overview")["value"]["computer_placement"] is True
    view = send("desktop.read", "computers.placement")["value"]
    assert [c["consumer"] for c in view["consumers"]] == ["rem"]
    assert view["order"] == [c["id"] for c in view["computers"]]
    placed = send(
        "desktop.action",
        "computers.place",
        {"consumer": "rem", "strategy": "manual", "computers": [PEER], "shared": [PEER]},
    )["value"]
    rem = placed["consumers"][0]
    assert rem["strategy"] == "manual" and rem["computers"] == [PEER] and rem["shared"] == [PEER]
    assert send("desktop.read", "computers.placement")["value"]["consumers"][0] == rem
    # The desktop refuses an unknown computer or consumer with the reason.
    unknown = send(
        "desktop.action",
        "computers.place",
        {"consumer": "rem", "strategy": "manual", "computers": ["missing"]},
    )
    assert unknown["code"] == "invalid" and "No computer with the ID missing" in unknown["detail"]
    org = send(
        "desktop.action", "computers.place", {"consumer": "org:gone", "strategy": "balanced"}
    )
    assert org["code"] == "invalid"
    # The protocol schema refuses an unknown strategy or field.
    assert (
        send("desktop.action", "computers.place", {"consumer": "rem", "strategy": "x"})["ok"]
        is False
    )
    extra = {"consumer": "rem", "strategy": "balanced", "order": [PEER]}
    assert send("desktop.action", "computers.place", extra)["ok"] is False
    app.desktop.enable(peer, False)
    assert send("desktop.read", "computers.placement")["code"] == "unauthorized"


def test_the_placement_fixture_matches_a_real_reading(tmp_path: Path) -> None:
    fixture = Path(module.__file__).parents[1] / (
        "devices/protocol/v2/fixtures/desktop.result.computers-placement.json"
    )
    body = json.loads(json.loads(fixture.read_text())["payload"]["body"])
    real = service(tmp_path, fleet=[desktop(False)]).placement()
    assert body.keys() == real.keys()
    assert body["consumers"][0].keys() == real["consumers"][0].keys()
    assert body["computers"][0].keys() == real["computers"][0].keys()


def test_the_result_fixture_matches_a_real_reading(tmp_path: Path) -> None:
    fixture = Path(module.__file__).parents[1] / (
        "devices/protocol/v2/fixtures/desktop.result.computers-list.json"
    )
    body = json.loads(json.loads(fixture.read_text())["payload"]["body"])
    real = service(tmp_path, fleet=[desktop(False)]).read()
    assert body.keys() == real.keys()
    # Every reading, this computer's or a paired desktop's, carries the same fields.
    assert {tuple(sorted(c)) for c in real["computers"]} == {
        tuple(sorted(c)) for c in body["computers"]
    }


def test_the_phones_test_copy_matches_the_result_fixture() -> None:
    root = Path(module.__file__).parents[3]
    fixture = root / "src/gofer/devices/protocol/v2/fixtures/desktop.result.computers-list.json"
    phone = root / "mobile/android/app/src/test/resources/desktop/computers-list.json"
    body = json.loads(json.loads(fixture.read_text())["payload"]["body"])
    assert json.loads(phone.read_text()) == body


def test_the_phones_test_copy_matches_the_placement_fixture() -> None:
    root = Path(module.__file__).parents[3]
    fixtures = root / "src/gofer/devices/protocol/v2/fixtures"
    fixture = fixtures / "desktop.result.computers-placement.json"
    phone = root / "mobile/android/app/src/test/resources/desktop/computers-placement.json"
    body = json.loads(json.loads(fixture.read_text())["payload"]["body"])
    assert json.loads(phone.read_text()) == body


def test_a_paired_desktops_reading_gives_its_limit_load_and_room(tmp_path: Path) -> None:
    reported = {
        **desktop(True),
        "load": {
            "max_threads": 8,
            "running_threads": 3,
            "cpu_pressure": 12.5,
            "memory_pressure": None,
        },
    }
    paired = by_id(service(tmp_path, fleet=[reported]).read(), PEER)
    assert paired["max_threads"] == 8 and paired["running_threads"] == 3
    assert paired["cpu_pressure"] == 12.5 and paired["memory_pressure"] is None
    assert paired["effective_threads"] == 8 and paired["capacity"] == 5
    # Offline, its last reading no longer counts.
    gone = by_id(service(tmp_path, fleet=[{**reported, "reachability": "unknown"}]).read(), PEER)
    assert gone["capacity"] == 0 and gone["max_threads"] is None
    # A malformed reading is treated as none.
    bad = {**reported, "load": {"max_threads": "8", "running_threads": 3}}
    assert by_id(service(tmp_path, fleet=[bad]).read(), PEER)["max_threads"] is None


def test_this_computers_shared_reading(tmp_path: Path) -> None:
    write_settings(slots_root(tmp_path), SlotSettings(max_workers=5))
    registry = service(tmp_path, running={"rem": 2, "phone": 1}, pressure=(33.333, None))
    assert registry.load_reading() == {
        "max_threads": 5,
        "running_threads": 3,
        "cpu_pressure": 33.33,
        "memory_pressure": None,
        "memory_hold": None,
        # Counts by kind only: what the workers are stays on this computer.
        "running_by_kind": {"rem": 2, "phone": 1, "organizations": 0, "swarms": 0},
    }


def test_this_computer_has_no_room_while_the_memory_hold_is_on(tmp_path: Path, monkeypatch) -> None:
    from tests.unit import test_ui_server as ui_tests

    hold: list[str | None] = ["0.9 GB available"]
    running = {"rem": 1}

    def held() -> ComputerService:
        registry = service(tmp_path, running=running)
        registry.memory = lambda: hold[0]
        return registry

    real = ui_tests._fake_server

    def with_computers(path: Path, **options: Any) -> Any:
        server = real(path, **options)
        server.computers = held()
        return server

    monkeypatch.setattr(ui_tests, "_fake_server", with_computers)
    write_settings(slots_root(tmp_path), SlotSettings(max_workers=4))
    listed = ui_tests._request(tmp_path, "GET", "/api/computers")
    assert listed.status == 200
    this = cast(dict[str, Any], listed.json())["computers"][0]
    assert this["this_computer"] is True and this["capacity"] == 0
    assert this["memory_hold"] == "0.9 GB available"
    # Paired desktops see the hold in this computer's shared reading.
    assert held().load_reading()["memory_hold"] == "0.9 GB available"
    # The first worker on a computer always starts: with none running there is room.
    running.clear()
    idle = held().read()["computers"][0]
    assert idle["capacity"] == 4 and idle["memory_hold"] is None
    assert held().load_reading()["memory_hold"] is None
    # Memory frees up: the room comes back.
    running["rem"] = 1
    hold[0] = None
    assert held().read()["computers"][0]["capacity"] == 3


def test_a_paired_desktop_whose_reading_carries_the_memory_hold_has_no_room(
    tmp_path: Path,
) -> None:
    load = {"max_threads": 8, "running_threads": 1, "cpu_pressure": None, "memory_pressure": None}
    held = {**desktop(True), "load": {**load, "memory_hold": "swap is 85% full"}}
    paired = by_id(service(tmp_path, fleet=[held]).read(), PEER)
    assert paired["capacity"] == 0 and paired["memory_hold"] == "swap is 85% full"
    assert paired["max_threads"] == 8 and paired["effective_threads"] == 8
    # An older desktop sends no hold and keeps its room.
    older = by_id(service(tmp_path, fleet=[{**desktop(True), "load": load}]).read(), PEER)
    assert older["capacity"] == 7 and older["memory_hold"] is None
    # Odd text from a desktop is never shown: it reads as no hold.
    for bad in (True, 3, "", "\x1b[31m"):
        odd = {**desktop(True), "load": {**load, "memory_hold": bad}}
        assert by_id(service(tmp_path, fleet=[odd]).read(), PEER)["memory_hold"] is None
    # Placement gives a held desktop no room.
    from gofer.core.placement_reader import PlacementReader

    class Registry:
        def read(self) -> dict[str, Any]:
            return service(tmp_path, fleet=[held]).read()

    reader = PlacementReader(tmp_path)
    reader.computers = Registry()
    reader.ask()
    reader.refresh()
    snapshot = reader.registry_snapshot("rem")
    assert snapshot is not None
    desk = next(c for c in snapshot.computers if c.id == PEER)
    assert desk.memory_hold == "swap is 85% full" and desk.room() == 0
