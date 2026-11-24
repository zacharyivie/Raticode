"""Placement settings: strategies, manual computers, sharing on purpose, saving and reading."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from gofer.core import placement_settings as placement
from gofer.core.computers import ComputerService
from gofer.core.placement_settings import PlacementError

THIS = "00000000-0000-4000-8000-000000000001"
LAPTOP = "00000000-0000-4000-8000-0000000000aa"
TOWER = "00000000-0000-4000-8000-0000000000bb"
MINI = "00000000-0000-4000-8000-0000000000cc"
ACME, BETA, GAMMA = "org-acme", "org-beta", "org-gamma"


def desktop(device_id: str, name: str, online: bool = True) -> dict[str, Any]:
    return {
        "device_id": device_id,
        "name": name,
        "role": "desktop",
        "state": "active",
        "reachability": "reachable" if online else "unknown",
        "last_seen": 1_899_999_000,
        "running_jobs": [],
    }


class Fakes:
    """Fake paired desktops and organizations the test can change between calls."""

    def __init__(self) -> None:
        self.fleet = [
            desktop(LAPTOP, "Laptop"),
            desktop(TOWER, "Tower"),
            desktop(MINI, "Mini", online=False),
        ]
        self.organizations = [
            {"id": ACME, "name": "Acme"},
            {"id": BETA, "name": "Beta"},
            {"id": GAMMA, "name": "Gamma"},
        ]


@pytest.fixture
def fakes() -> Fakes:
    return Fakes()


@pytest.fixture
def service(tmp_path: Path, fakes: Fakes) -> ComputerService:
    return ComputerService(
        tmp_path,
        name=lambda: "Studio",
        device_id=lambda: THIS,
        fleet=lambda: fakes.fleet,
        pressure=lambda: (None, None),
        clock=lambda: 1_900_000_000.0,
        organizations=lambda: fakes.organizations,
    )


def consumer(view: dict[str, Any], key: str) -> dict[str, Any]:
    return next(c for c in view["consumers"] if c["consumer"] == key)


def org(oid: str) -> str:
    return placement.organization_consumer(oid)


def test_every_consumer_starts_on_the_default_strategy(service: ComputerService) -> None:
    view = service.placement()
    assert view["strategies"] == ["overflow", "balanced", "manual"]
    assert [c["consumer"] for c in view["consumers"]] == ["rem", org(ACME), org(BETA), org(GAMMA)]
    rem = consumer(view, "rem")
    assert rem == {
        "consumer": "rem",
        "kind": "rem",
        "name": "Rem",
        "strategy": "overflow",
        "saved": False,
        "computers": [],
        "shared": [],
        "missing_computers": [],
        "notice": "",
    }
    assert consumer(view, org(ACME))["kind"] == "organization"
    assert consumer(view, org(ACME))["name"] == "Acme"
    # Offline computers can be assigned too; they are in the registry.
    assert {c["id"] for c in view["computers"]} == {THIS, LAPTOP, TOWER, MINI}


def test_the_example_layout_saves_and_reads_back(tmp_path: Path, service: ComputerService) -> None:
    """Rem on one computer, Acme on two, Beta and Gamma sharing a fourth."""
    service.set_placement("rem", "manual", [THIS])
    service.set_placement(org(ACME), "manual", [LAPTOP, TOWER])
    service.set_placement(org(BETA), "manual", [MINI], [MINI])
    view = service.set_placement(org(GAMMA), "manual", [MINI], [MINI])
    assert consumer(view, "rem")["computers"] == [THIS]
    assert consumer(view, org(ACME))["computers"] == [LAPTOP, TOWER]
    assert consumer(view, org(GAMMA))["shared"] == [MINI]
    # A fresh read from the saved file gives the same placements.
    assert service.placement()["consumers"] == view["consumers"]
    saved = json.loads((tmp_path / "computers.json").read_text())["placement"]
    assert saved["consumers"]["rem"] == {"strategy": "manual", "computers": [THIS], "shared": []}
    assert saved["consumers"][org(BETA)]["shared"] == [MINI]


def test_a_manual_placement_with_only_paired_desktops_saves_with_a_notice(
    tmp_path: Path, service: ComputerService
) -> None:
    """Work starts only on this computer for now, so such a placement saves but warns."""
    view = service.set_placement("rem", "manual", [LAPTOP])
    rem = consumer(view, "rem")
    assert rem["computers"] == [LAPTOP]
    assert rem["notice"] == (
        "Rem's work starts only on this computer for now, so with only Laptop picked, every "
        "task waits. A waiting research or conversation task can still go to Laptop when you "
        "pick Run there."
    )
    saved = json.loads((tmp_path / "computers.json").read_text())["placement"]
    assert saved["consumers"]["rem"]["computers"] == [LAPTOP]
    view = service.set_placement(org(ACME), "manual", [TOWER, MINI])
    assert consumer(view, org(ACME))["notice"] == (
        "Organization workers start only on this computer for now, so with only Tower and "
        "Mini picked, its tickets wait unless they run through a fleet execution target there."
    )
    # A fresh read gives the same notice.
    assert consumer(service.placement(), "rem")["notice"] == rem["notice"]


def test_a_placement_that_can_start_here_has_no_notice(service: ComputerService) -> None:
    view = service.set_placement("rem", "manual", [LAPTOP, THIS])
    assert consumer(view, "rem")["notice"] == ""
    view = service.set_placement(org(ACME), "balanced")
    assert consumer(view, org(ACME))["notice"] == ""
    assert consumer(view, org(BETA))["notice"] == ""


def test_a_placement_left_with_only_unpaired_computers_still_warns(
    service: ComputerService, fakes: Fakes
) -> None:
    service.set_placement(org(ACME), "manual", [TOWER])
    fakes.fleet = [d for d in fakes.fleet if d["device_id"] != TOWER]
    assert consumer(service.placement(), org(ACME))["notice"] == (
        "Organization workers start only on this computer for now, so with only unpaired "
        "computers picked, its tickets wait unless they run through a fleet execution target "
        "on a paired desktop."
    )


def test_overflow_and_balanced_save_without_computers(service: ComputerService) -> None:
    view = service.set_placement(org(ACME), "balanced")
    entry = consumer(view, org(ACME))
    assert entry["strategy"] == "balanced" and entry["saved"] is True
    assert consumer(service.set_placement("rem", "overflow"), "rem")["saved"] is True


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("rem", "nearest"), "strategy must be one of overflow, balanced, manual"),
        (("rem", "manual"), "needs at least one computer"),
        (("rem", "manual", []), "needs at least one computer"),
        (("rem", "manual", ["missing"]), "No computer with the ID missing"),
        (("rem", "manual", [THIS, "  "]), "empty computer ID"),
        (("rem", "manual", THIS), "must be a list of computer IDs"),
        (("rem", "balanced", [THIS]), "Only manual placement takes computers"),
        (("rem", "manual", [THIS], [LAPTOP]), "isn't one of this placement's"),
        (("org:missing", "overflow"), "No consumer org:missing"),
        ((ACME, "overflow"), "No consumer org-acme"),
        (("", "overflow"), "Send consumer"),
        ((None, "overflow"), "Send consumer"),
    ],
)
def test_invalid_placements_are_refused_with_the_reason(
    tmp_path: Path, service: ComputerService, args: tuple[Any, ...], message: str
) -> None:
    with pytest.raises(PlacementError, match=message):
        service.set_placement(*args)
    assert not (tmp_path / "computers.json").exists() or "placement" not in json.loads(
        (tmp_path / "computers.json").read_text()
    )


def test_a_computer_in_two_manual_placements_must_be_shared_by_both(
    service: ComputerService,
) -> None:
    service.set_placement(org(ACME), "manual", [LAPTOP, TOWER])
    with pytest.raises(PlacementError, match="Laptop is already assigned to Acme; mark it shared"):
        service.set_placement(org(BETA), "manual", [LAPTOP])
    with pytest.raises(PlacementError, match="assigned to Acme without sharing"):
        service.set_placement(org(BETA), "manual", [LAPTOP], [LAPTOP])
    with pytest.raises(PlacementError, match="Tower is already assigned to Acme"):
        service.set_placement("rem", "manual", [TOWER])
    # Once Acme shares the laptop, Beta can share it; the tower stays Acme's alone.
    service.set_placement(org(ACME), "manual", [LAPTOP, TOWER], [LAPTOP])
    view = service.set_placement(org(BETA), "manual", [LAPTOP], [LAPTOP])
    assert consumer(view, org(BETA))["computers"] == [LAPTOP]
    # Acme can't stop sharing while Beta still uses the laptop.
    with pytest.raises(PlacementError, match="Laptop is already assigned to Beta"):
        service.set_placement(org(ACME), "manual", [LAPTOP, TOWER])
    # A consumer on overflow or balanced doesn't hold any computer.
    service.set_placement(org(BETA), "balanced")
    service.set_placement(org(ACME), "manual", [LAPTOP, TOWER])


def test_a_refused_save_leaves_the_saved_placements_alone(service: ComputerService) -> None:
    service.set_placement("rem", "manual", [THIS])
    before = service.placement()
    with pytest.raises(PlacementError):
        service.set_placement("rem", "manual", ["missing"])
    assert service.placement() == before


def test_repeated_ids_are_saved_once_in_order(service: ComputerService) -> None:
    view = service.set_placement("rem", "manual", [TOWER, THIS, TOWER], [TOWER, TOWER])
    assert consumer(view, "rem")["computers"] == [TOWER, THIS]
    assert consumer(view, "rem")["shared"] == [TOWER]


def test_an_unpaired_computer_shows_as_missing_and_must_go_at_the_next_save(
    service: ComputerService, fakes: Fakes
) -> None:
    service.set_placement(org(ACME), "manual", [LAPTOP, TOWER])
    fakes.fleet = [d for d in fakes.fleet if d["device_id"] != TOWER]
    entry = consumer(service.placement(), org(ACME))
    assert entry["computers"] == [LAPTOP, TOWER] and entry["missing_computers"] == [TOWER]
    with pytest.raises(PlacementError, match="No computer with the ID"):
        service.set_placement(org(ACME), "manual", [LAPTOP, TOWER])
    entry = consumer(service.set_placement(org(ACME), "manual", [LAPTOP]), org(ACME))
    assert entry["missing_computers"] == []


def test_a_deleted_organization_stops_showing_and_is_dropped_at_the_next_save(
    tmp_path: Path, service: ComputerService, fakes: Fakes
) -> None:
    service.set_placement(org(GAMMA), "manual", [MINI])
    fakes.organizations = [o for o in fakes.organizations if o["id"] != GAMMA]
    assert org(GAMMA) not in {c["consumer"] for c in service.placement()["consumers"]}
    # Its old computer is free for others; a deleted organization holds nothing.
    service.set_placement(org(BETA), "manual", [MINI])
    saved = json.loads((tmp_path / "computers.json").read_text())["placement"]["consumers"]
    assert set(saved) == {org(BETA)}


def test_saving_keeps_the_computer_order_and_the_registry_store(
    tmp_path: Path, service: ComputerService
) -> None:
    service.read()  # The registry saves this computer's state first.
    store = json.loads((tmp_path / "computers.json").read_text())
    store["placement"] = {"order": [TOWER, LAPTOP], "consumers": {}}
    (tmp_path / "computers.json").write_text(json.dumps(store))
    service.set_placement("rem", "manual", [THIS])
    after = json.loads((tmp_path / "computers.json").read_text())
    assert after["placement"]["order"] == [TOWER, LAPTOP]
    assert after["seen"] == store["seen"]
    assert oct((tmp_path / "computers.json").stat().st_mode & 0o777) == "0o600"


def test_unreadable_saved_entries_read_as_the_default(
    tmp_path: Path, service: ComputerService
) -> None:
    (tmp_path / "computers.json").write_text(
        json.dumps(
            {
                "placement": {
                    "consumers": {
                        "rem": {"strategy": "manual", "computers": []},
                        org(ACME): {"strategy": "nearest"},
                        org(BETA): {"strategy": "manual", "computers": "x"},
                        org(GAMMA): {"strategy": "balanced", "computers": [LAPTOP]},
                    }
                }
            }
        )
    )
    view = service.placement()
    for key in ("rem", org(ACME), org(BETA)):
        assert consumer(view, key)["strategy"] == "overflow"
        assert consumer(view, key)["saved"] is False
    assert consumer(view, org(GAMMA))["strategy"] == "balanced"
    assert consumer(view, org(GAMMA))["computers"] == []


def test_validate_checks_one_placement_without_saving() -> None:
    known = [{"id": THIS, "name": "Studio"}, {"id": LAPTOP, "name": "Laptop"}]
    orgs = [{"id": ACME, "name": "Acme"}]
    others = {"rem": {"strategy": "manual", "computers": [THIS], "shared": [THIS]}}
    entry = placement.validate(
        org(ACME),
        "manual",
        [THIS, LAPTOP],
        [THIS],
        known_computers=known,
        organizations=orgs,
        others=others,
    )
    assert entry == {"strategy": "manual", "computers": [THIS, LAPTOP], "shared": [THIS]}
    with pytest.raises(PlacementError, match="Studio is already assigned to Rem"):
        placement.validate(
            org(ACME), "manual", [THIS], known_computers=known, organizations=orgs, others=others
        )


def test_organization_names_list_each_id_and_name_for_placement(tmp_path: Path) -> None:
    from gofer.ui.organization_store import OrganizationStore

    store = OrganizationStore(tmp_path / "data")
    first = store.create("", {"name": "Acme", "employees": []}, "user", "Create")
    second = store.create("", {"name": "Beta", "employees": []}, "user", "Create")
    assert store.names() == [
        {"id": first["id"], "name": "Acme"},
        {"id": second["id"], "name": "Beta"},
    ]


def test_the_computer_order_saves_and_lists_computers_in_it(
    tmp_path: Path, service: ComputerService
) -> None:
    # Before any order is saved, computers come as the registry lists them.
    assert service.placement()["order"] == [THIS, LAPTOP, TOWER, MINI]
    service.set_placement("rem", "manual", [THIS])
    view = service.set_order([TOWER, LAPTOP])
    # Computers the order leaves out keep their place after the named ones.
    assert view["order"] == [TOWER, LAPTOP, THIS, MINI]
    assert [c["id"] for c in view["computers"]] == [TOWER, LAPTOP, THIS, MINI]
    assert consumer(view, "rem")["computers"] == [THIS]
    saved = json.loads((tmp_path / "computers.json").read_text())["placement"]
    assert saved["order"] == [TOWER, LAPTOP]
    assert saved["consumers"]["rem"]["computers"] == [THIS]
    # A fresh read and a later placement save keep the order.
    assert service.placement()["order"] == [TOWER, LAPTOP, THIS, MINI]
    assert service.set_placement(org(ACME), "balanced")["order"][0] == TOWER


def test_a_computer_unpaired_after_the_order_was_saved_drops_out_of_it(
    service: ComputerService, fakes: Fakes
) -> None:
    service.set_order([MINI, TOWER, THIS])
    fakes.fleet = [d for d in fakes.fleet if d["device_id"] != TOWER]
    assert service.placement()["order"] == [MINI, THIS, LAPTOP]


@pytest.mark.parametrize(
    ("order", "message"),
    [
        ([THIS, "missing"], "No computer with the ID missing"),
        ([], "at least one computer ID"),
        (None, "Send order"),
        (THIS, "must be a list of computer IDs"),
        ([THIS, " "], "empty computer ID"),
        ([THIS, 3], "must be a list of computer IDs"),
    ],
)
def test_an_order_with_an_unknown_computer_id_is_refused(
    tmp_path: Path, service: ComputerService, order: Any, message: str
) -> None:
    service.set_order([LAPTOP])
    before = (tmp_path / "computers.json").read_text()
    with pytest.raises(PlacementError, match=message):
        service.set_order(order)
    assert (tmp_path / "computers.json").read_text() == before


def test_settings_api_saves_the_order_and_refuses_an_unknown_computer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fakes: Fakes
) -> None:
    from tests.unit import test_ui_server as ui_tests

    real = ui_tests._fake_server

    def with_computers(path: Path, **options: Any) -> Any:
        server = real(path, **options)
        server.computers = ComputerService(
            path / "computers",
            name=lambda: "Studio",
            device_id=lambda: THIS,
            fleet=lambda: fakes.fleet,
            pressure=lambda: (None, None),
            clock=lambda: 1_900_000_000.0,
            organizations=lambda: fakes.organizations,
        )
        return server

    monkeypatch.setattr(ui_tests, "_fake_server", with_computers)
    url = "/api/computers/placement"
    saved = ui_tests._request(tmp_path, "POST", url, body={"order": [LAPTOP, THIS]})
    assert saved.status == 200
    assert cast(dict[str, Any], saved.json())["order"][:2] == [LAPTOP, THIS]
    read = cast(dict[str, Any], ui_tests._request(tmp_path, "GET", url).json())
    assert read["order"][:2] == [LAPTOP, THIS]
    unknown = ui_tests._request(tmp_path, "POST", url, body={"order": ["missing"]})
    assert unknown.status == 400
    assert "No computer with the ID missing" in str(unknown.json())
    # The order goes alone; mixing it with a placement is refused.
    mixed = ui_tests._request(
        tmp_path, "POST", url, body={"order": [THIS], "consumer": "rem", "strategy": "overflow"}
    )
    assert mixed.status == 400
    placed = ui_tests._request(
        tmp_path, "POST", url, body={"consumer": "rem", "strategy": "balanced"}
    )
    assert consumer(cast(dict[str, Any], placed.json()), "rem")["strategy"] == "balanced"
