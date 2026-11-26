"""Rem task and organization routine cron schedules read weekdays the crontab way.

New saves store day names. Schedules saved before that, which ran with APScheduler's
Monday = 0 numbering, are rewritten as names on load and keep their days.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from gofer.rattish.triggers import cron_with_day_names, saved_cron_day_names
from gofer.ui.organization_operations import Routine, next_occurrence
from gofer.ui.organization_store import OrganizationStore
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_home_models import Schedule
from gofer.ui.rem_home_store import RemHomeStore
from tests.unit.test_organizations import call, company
from tests.unit.test_organizations import manager as manager
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_home import recipe


def cron_task(home: RemHome, project: Path, cron: str) -> dict[str, Any]:
    created = home.create_task(
        {
            "request_id": f"create-{uuid4()}",
            **recipe(
                project,
                title="Weekly digest",
                type="maintain",
                prompt="Write the weekly digest.",
                schedule={"kind": "cron", "cron": cron, "timezone": "UTC"},
            ),
        }
    )
    return dict(created["task"])


def weekdays_of(schedule: Schedule, count: int = 10) -> set[int]:
    """Python weekdays (Monday = 0) of the schedule's next ``count`` runs."""
    seen: set[int] = set()
    clock = datetime.now(UTC)
    for _ in range(count):
        upcoming = schedule.next(clock)
        assert upcoming is not None
        moment = datetime.fromisoformat(upcoming)
        seen.add(moment.astimezone(UTC).weekday())
        clock = moment
    return seen


def write_legacy_task(home: RemHome, task_id: str, cron: str) -> None:
    """Put cron text back as a build before this change stored it, numbers and all."""
    with home.store.connect() as db:
        task = home.store.get(db, "tasks", task_id)
        task["schedule"]["cron"] = cron
        home.store.put(db, "tasks", task)


def test_new_rem_task_reads_crontab_weekdays_and_stores_day_names(
    home: RemHome, tmp_path: Path
) -> None:
    task = cron_task(home, tmp_path, "0 9 * * 1")
    assert task["schedule"]["cron"] == "0 9 * * mon"
    assert datetime.fromisoformat(task["next_occurrence_at"]).weekday() == 0
    assert weekdays_of(Schedule.saved(task["schedule"])) == {0}
    # 0 and 7 are both Sunday, and a range of three or more days reads as one.
    assert Schedule.model_validate({"kind": "cron", "cron": "0 9 * * 0"}).cron == "0 9 * * sun"
    assert Schedule.model_validate({"kind": "cron", "cron": "0 9 * * 7"}).cron == "0 9 * * sun"
    assert (
        Schedule.model_validate({"kind": "cron", "cron": "0 9 * * 1-5"}).cron == "0 9 * * mon-fri"
    )
    # New saves get the crontab checks.
    for cron, message in (
        ("0 9 1 * 1", "not both"),
        ("0 9 31 2 *", "never runs"),
        ("0 9 * * 8", "isn't valid"),
    ):
        with pytest.raises(ValidationError, match=message):
            Schedule.model_validate({"kind": "cron", "cron": cron})


def test_new_organization_routine_fires_monday_to_friday(manager: Any, tmp_path: Path) -> None:
    org = company(manager, tmp_path)
    routine = call(
        manager,
        org,
        "routine_save",
        routine={"name": "Standup", "task": {"title": "Standup"}, "cron": "0 9 * * 1-5"},
    )
    assert routine["cron"] == "0 9 * * mon-fri"
    seen: set[int] = set()
    at = datetime.now(UTC)
    for _ in range(15):
        upcoming = next_occurrence(routine, at)
        assert upcoming is not None
        at = datetime.fromisoformat(upcoming)
        seen.add(at.weekday())
    assert seen == {0, 1, 2, 3, 4}
    with pytest.raises(ValidationError, match="not both"):
        Routine.model_validate({"name": "x", "task": {"title": "x"}, "cron": "0 9 1 * 1"})


def test_rem_task_saved_with_numbered_weekday_keeps_its_day_after_loading(
    home: RemHome, tmp_path: Path
) -> None:
    task = cron_task(home, tmp_path, "0 9 * * 1")
    # Saved before this change, 1 ran on Tuesday (APScheduler counts from Monday = 0).
    write_legacy_task(home, task["id"], "0 9 * * 1")
    RemHomeStore(home.store.path.parent)
    with home.store.read() as db:
        loaded = home.store.get(db, "tasks", task["id"])
    assert loaded["schedule"]["cron"] == "0 9 * * tue"
    assert loaded["revision"] == task["revision"] + 1
    assert weekdays_of(Schedule.saved(loaded["schedule"])) == {1}
    # Loading again changes nothing.
    RemHomeStore(home.store.path.parent)
    with home.store.read() as db:
        assert home.store.get(db, "tasks", task["id"]) == loaded


def test_saved_day_of_month_and_weekday_keeps_running_but_new_saves_refuse_it(
    home: RemHome, tmp_path: Path
) -> None:
    task = cron_task(home, tmp_path, "0 9 1 * *")
    write_legacy_task(home, task["id"], "0 9 1 * 1")
    RemHomeStore(home.store.path.parent)
    with home.store.read() as db:
        loaded = home.store.get(db, "tasks", task["id"])
    assert loaded["schedule"]["cron"] == "0 9 1 * tue"
    # It runs as it always did: a 1st of the month that is a Tuesday.
    upcoming = datetime.fromisoformat(Schedule.saved(loaded["schedule"]).next(datetime.now(UTC)))
    assert (upcoming.day, upcoming.weekday()) == (1, 1)
    # Editing something else keeps the schedule.
    edited = home.edit_task(
        task["id"],
        {"request_id": "rename", "expected_revision": loaded["revision"], "title": "Renamed"},
        "edit",
    )
    assert edited["task"]["schedule"]["cron"] == "0 9 1 * tue"
    # Changing the cron text gets the new check.
    with pytest.raises(ValidationError, match="not both"):
        home.edit_task(
            task["id"],
            {
                "request_id": "reschedule",
                "expected_revision": edited["task"]["revision"],
                "schedule": {"kind": "cron", "cron": "0 9 2 * 1", "timezone": "UTC"},
            },
            "edit",
        )


def test_routine_saved_with_numbered_weekdays_keeps_its_days_after_loading(
    manager: Any, tmp_path: Path
) -> None:
    org = company(manager, tmp_path)
    routine = call(
        manager,
        org,
        "routine_save",
        routine={"name": "Standup", "task": {"title": "Standup"}, "cron": "0 9 * * mon-fri"},
    )
    path = manager.store.path
    manager.close()
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE organization_records SET body=? WHERE kind='routines' AND id=?",
            (json.dumps({**routine, "cron": "0 9 * * 0-4"}), routine["id"]),
        )
    OrganizationStore(path.parent)
    OrganizationStore(path.parent)  # Safe to run more than once.
    with sqlite3.connect(path) as db:
        body = db.execute(
            "SELECT body FROM organization_records WHERE kind='routines' AND id=?",
            (routine["id"],),
        ).fetchone()[0]
    loaded = json.loads(body)
    assert loaded["cron"] == "0 9 * * mon-fri"
    assert loaded["revision"] == routine["revision"] + 1


def test_legacy_conversion_reads_apscheduler_numbers_and_leaves_names() -> None:
    assert saved_cron_day_names("0 9 * * 5-6") == "0 9 * * sun,sat"
    assert saved_cron_day_names("0 9 * * */2") == "0 9 * * sun,mon,wed,fri"
    assert saved_cron_day_names("0 9 * * 0-6") == "0 9 * * *"
    assert saved_cron_day_names("0 9 * * mon,tue") == "0 9 * * mon,tue"
    assert saved_cron_day_names("0 9 * * 7") == "0 9 * * 7"  # Never valid; left alone.
    assert cron_with_day_names("0 9 * * sat,0") == "0 9 * * sun,sat"


def test_rem_task_saved_with_a_range_ending_on_sunday_keeps_its_days(
    home: RemHome, tmp_path: Path
) -> None:
    task = cron_task(home, tmp_path, "0 9 * * sat")
    # APScheduler puts Sunday last, so it ran sat-sun; crontab can't read that range.
    write_legacy_task(home, task["id"], "0 9 * * sat-sun")
    RemHomeStore(home.store.path.parent)
    with home.store.read() as db:
        loaded = home.store.get(db, "tasks", task["id"])
    assert loaded["schedule"]["cron"] == "0 9 * * sun,sat"
    assert weekdays_of(Schedule.saved(loaded["schedule"])) == {5, 6}
    RemHomeStore(home.store.path.parent)
    with home.store.read() as db:
        assert home.store.get(db, "tasks", task["id"]) == loaded


def test_routine_saved_with_a_range_ending_on_sunday_still_fires(
    manager: Any, tmp_path: Path
) -> None:
    org = company(manager, tmp_path)
    routine = call(
        manager,
        org,
        "routine_save",
        routine={"name": "Weekend", "task": {"title": "Weekend"}, "cron": "0 9 * * fri"},
    )
    path = manager.store.path
    manager.close()
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE organization_records SET body=? WHERE kind='routines' AND id=?",
            (json.dumps({**routine, "cron": "0 9 * * fri-sun"}), routine["id"]),
        )
    OrganizationStore(path.parent)
    with sqlite3.connect(path) as db:
        body = db.execute(
            "SELECT body FROM organization_records WHERE kind='routines' AND id=?",
            (routine["id"],),
        ).fetchone()[0]
    loaded = json.loads(body)
    assert loaded["cron"] == "0 9 * * sun,fri,sat"
    seen: set[int] = set()
    at = datetime.now(UTC)
    for _ in range(9):
        upcoming = next_occurrence(loaded, at)
        assert upcoming is not None
        at = datetime.fromisoformat(upcoming)
        seen.add(at.weekday())
    assert seen == {4, 5, 6}


def test_a_schedule_that_cant_be_read_stops_only_its_own_task(
    home: RemHome, tmp_path: Path
) -> None:
    broken = cron_task(home, tmp_path, "0 9 * * mon")
    daily = home.create_task(
        {
            "request_id": "daily",
            **recipe(
                tmp_path,
                title="Daily digest",
                type="maintain",
                prompt="Write the daily digest.",
                schedule={"kind": "daily", "local_time": "07:30", "timezone": "UTC"},
            ),
        }
    )["task"]
    past = "2026-01-05T09:00:00+00:00"
    with home.store.connect() as db:
        for identity, cron in ((broken["id"], "0 25 * * *"), (daily["id"], None)):
            task = home.store.get(db, "tasks", identity)
            if cron:
                task["schedule"]["cron"] = cron
            task["next_occurrence_at"] = past
            home.store.put(db, "tasks", task)
    home.store.due(datetime.now(UTC))
    with home.store.read() as db:
        assert home.store.get(db, "tasks", broken["id"])["next_occurrence_at"] is None
        moved = home.store.get(db, "tasks", daily["id"])["next_occurrence_at"]
        assert moved and datetime.fromisoformat(moved) > datetime.now(UTC)
        ran = db.execute("SELECT task FROM runs WHERE task IN (?, ?)", (broken["id"], daily["id"]))
        assert {row[0] for row in ran} == {daily["id"]}
    routine = {"id": "r", "cron": "0 25 * * *", "timezone": "UTC"}
    assert next_occurrence(routine, datetime.now(UTC)) is None


def test_legacy_conversion_reads_name_ranges_ending_on_sunday() -> None:
    assert saved_cron_day_names("0 9 * * fri-sun") == "0 9 * * sun,fri,sat"
    assert saved_cron_day_names("0 9 * * mon-sun/2") == "0 9 * * sun,mon,wed,fri"
    # Names crontab reads mean the same days to APScheduler, so they stay.
    assert saved_cron_day_names("0 9 * * mon-fri") == "0 9 * * mon-fri"
    assert saved_cron_day_names("0 9 * * sun,fri,sat") == "0 9 * * sun,fri,sat"
