"""Organizations security review, 13 November 2026.

- Rem, or an agent token acting as Rem, could point an organization's Second Brain at any
  folder without the user's consent. Employees reach it through a tool that runs outside
  their sandbox and reads and writes notes there.
- Asking again for the same change refreshed the waiting request in place even when the
  change had come to mean more, so approving the request the user saw (or "approve those"
  in chat) applied the wider meaning.
- The consent summary said "add project folders" without naming them.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.organization_auto import config_risks
from gofer.ui.organizations import OrganizationManager, escalation, starting_details


async def no_runs(**options: Any) -> Any:
    raise AssertionError("No employee runs in these tests")
    yield {}


def manager_for(tmp_path: Path) -> OrganizationManager:
    return OrganizationManager(tmp_path / "data", stream=no_runs, start_runtime=False)


def company(manager: OrganizationManager, root: Path) -> Any:
    return manager.call(
        str(root),
        "user",
        {
            "action": "create",
            "params": {
                "config": {
                    "name": "Studio",
                    "slug": "studio",
                    "employees": [{"id": "lead", "name": "Avery Lane"}],
                    "projects": [{"id": "desktop", "name": "Desktop"}],
                }
            },
        },
    )


def configure(manager: OrganizationManager, org: dict[str, Any], actor: str, config: Any) -> Any:
    current = manager.store.get("", org["id"])
    return manager.call(
        org["projectRoot"],
        actor,
        {
            "organizationId": org["id"],
            "action": "configure",
            "params": {
                "config": config,
                "expectedRevision": current["revision"],
                "reason": "Change it",
            },
        },
    )


def current_config(manager: OrganizationManager, org: dict[str, Any]) -> dict[str, Any]:
    config: dict[str, Any] = copy.deepcopy(manager.store.get("", org["id"])["config"])
    config.pop("packageFiles", None)
    return config


def requests(manager: OrganizationManager, org: dict[str, Any]) -> dict[str, Any]:
    return manager.store.get("", org["id"])["runtime"].get("consentRequests") or {}


@pytest.mark.parametrize("actor", ["Rem:thread", "Rem:token:ci"])
def test_rem_needs_consent_to_give_workers_a_second_brain_folder(
    tmp_path: Path, actor: str
) -> None:
    manager = manager_for(tmp_path)
    root = tmp_path / "project"
    root.mkdir()
    org = company(manager, root)
    elsewhere = tmp_path / "private-notes"
    elsewhere.mkdir()
    config = {
        **current_config(manager, org),
        "remSecondBrain": {"enabled": True, "root": str(elsewhere)},
    }

    asked = configure(manager, org, actor, config)

    assert asked["needsUserConsent"] is True
    assert str(elsewhere) in asked["summary"] and "Second Brain" in asked["summary"]
    assert manager.store.get("", org["id"])["config"]["remSecondBrain"] == {}
    # The user's approval applies it.
    manager.call(
        org["projectRoot"],
        "user",
        {
            "organizationId": org["id"],
            "action": "request_decide",
            "params": {"requestId": asked["requestId"], "decision": "approve"},
        },
    )
    saved = manager.store.get("", org["id"])["config"]["remSecondBrain"]
    assert saved["enabled"] is True and saved["root"] == str(elsewhere)
    # Moving it to another folder asks again; switching it off doesn't.
    moved = {**current_config(manager, org)}
    moved["remSecondBrain"] = {"enabled": True, "root": str(root)}
    assert configure(manager, org, actor, moved)["needsUserConsent"] is True
    off = {**current_config(manager, org), "remSecondBrain": {}}
    assert configure(manager, org, actor, off)["config"]["remSecondBrain"] == {}


def test_second_brain_folder_is_named_in_start_and_counts_as_danger_in_auto_mode() -> None:
    before: dict[str, Any] = {"remSecondBrain": {}}
    after: dict[str, Any] = {"remSecondBrain": {"enabled": True, "root": "/home/someone/notes"}}
    assert "Second Brain at /home/someone/notes" in escalation(before, after)
    assert "/home/someone/notes" in starting_details(after)
    danger, _ = config_risks(before, after)
    assert any("Second Brain" in item for item in danger)
    # The same folder, or none, is no change.
    assert escalation(after, after) == ""
    assert escalation(after, before) == ""
    assert config_risks(after, after)[0] == []


def test_added_project_folders_are_named_in_the_request() -> None:
    reason = escalation({"projectRoots": ["/work/a"]}, {"projectRoots": ["/work/a", "/work/b"]})
    assert "add project folders /work/b" in reason
    assert "/work/a" not in reason


def test_a_request_that_now_means_more_is_replaced_not_refreshed(tmp_path: Path) -> None:
    manager = manager_for(tmp_path)
    root = tmp_path / "project"
    root.mkdir()
    extra = tmp_path / "extra"
    extra.mkdir()
    org = company(manager, root)
    wanted = {**current_config(manager, org)}
    wanted["projectRoots"] = [*wanted["projectRoots"], str(extra)]

    first = configure(manager, org, "Rem", copy.deepcopy(wanted))
    assert first["needsUserConsent"] is True
    assert "more tools" not in first["summary"]

    # The user takes a tool away. Rem's same configuration would now hand it back.
    narrowed = current_config(manager, org)
    narrowed["employees"][0]["resources"] = {"shell": False}
    configure(manager, org, "user", narrowed)

    second = configure(manager, org, "Rem", copy.deepcopy(wanted))
    assert second["needsUserConsent"] is True
    assert second["requestId"] != first["requestId"]
    assert "more tools" in second["summary"]
    stored = requests(manager, org)
    assert stored[first["requestId"]]["status"] == "expired"
    assert stored[first["requestId"]]["replacedBy"] == second["requestId"]
    assert stored[second["requestId"]]["status"] == "pending"

    # Approving the request the user saw no longer applies anything.
    with pytest.raises(ValueError, match="no longer waiting"):
        manager.call(
            org["projectRoot"],
            "user",
            {
                "organizationId": org["id"],
                "action": "request_decide",
                "params": {"requestId": first["requestId"], "decision": "approve"},
            },
        )
    saved = manager.store.get("", org["id"])["config"]
    assert str(extra) not in saved["projectRoots"]
    assert saved["employees"][0]["resources"]["shell"] is False


def test_asking_again_for_the_same_meaning_still_refreshes_in_place(tmp_path: Path) -> None:
    manager = manager_for(tmp_path)
    root = tmp_path / "project"
    root.mkdir()
    extra = tmp_path / "extra"
    extra.mkdir()
    org = company(manager, root)
    wanted = {**current_config(manager, org)}
    wanted["projectRoots"] = [*wanted["projectRoots"], str(extra)]
    first = configure(manager, org, "Rem", copy.deepcopy(wanted))

    # An unrelated change moves the revision without changing what the request means.
    renamed = current_config(manager, org)
    renamed["description"] = "Now with a description"
    configure(manager, org, "user", renamed)

    second = configure(manager, org, "Rem", copy.deepcopy(wanted))
    assert second["requestId"] == first["requestId"]
    assert [r["status"] for r in requests(manager, org).values()] == ["pending"]
