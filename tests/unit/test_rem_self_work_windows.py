"""Timed self-work windows, the report of each self-work period, and task start conditions.

Fake providers and isolated data only (the ``board`` fixture from test_rem_kanban and the
``home`` fixture from test_rem_home). The user's time zone is pinned to America/New_York so
results don't depend on this computer's zone. Integration tests anchor self-work's clock to
the real time, because run and ticket records are stamped with the real time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from gofer.ui import rem_self_work_windows as windows
from gofer.ui import rem_task_after as after_tasks
from gofer.ui.rem_home import RemHome, self_work_shortcut
from gofer.ui.rem_home_models import ClockTime, Recipe, SelfWorkRequest
from gofer.ui.rem_home_store import RemHomeStore
from gofer.ui.rem_kanban import KanbanRefused
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_home import recipe
from tests.unit.test_rem_kanban import add, set_options, settle, tick, ticket
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_rem_pause_resume import reply_to, run_of, runs, start_task, until
from tests.unit.test_rem_task_after import gate, later, run_with, set_state, view
from tests.unit.test_rem_task_after import runs as task_runs

ZONE = "America/New_York"
UTC_ZONE = "UTC"
# Monday 9 November 2026, 12:00 UTC: 7:00 AM in New York, 8:00 PM in Irkutsk.
MONDAY = datetime(2026, 11, 9, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def user_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(windows, "default_zone", lambda: ZONE)


def clock_time(**fields: Any) -> ClockTime:
    return ClockTime.model_validate(fields)


def resolve(clock: datetime, zone: str | None = None, **fields: Any) -> datetime:
    return windows.resolve(clock_time(**fields), zone_name=zone, clock=clock, after=clock)


class Clock:
    """Self-work's clock, anchored to the real time and moved forward by hand."""

    def __init__(self, home: RemHome) -> None:
        self.start = datetime.now(UTC).replace(microsecond=0)
        self.offset = timedelta()
        home.self_work.started_at = self.start
        home.self_work.clock = self.now

    def now(self) -> datetime:
        return self.start + self.offset

    def to(self, **delta: float) -> datetime:
        self.offset = timedelta(**delta)
        return self.now()

    def at(self, **delta: float) -> str:
        return (self.start + timedelta(**delta)).isoformat()


def decide(home: RemHome, request: str, body: str, self_work: dict[str, Any]) -> str:
    """One chat turn in which the model returns ``self_work``; returns Rem's reply."""
    home.generate = lambda *args: {
        "reply": "Sure.",
        "tasks": [],
        "memories": [],
        "self_work": self_work,
    }
    return reply_to(home, request, body)


def reports(home: RemHome) -> list[dict[str, Any]]:
    return [m for m in home.store.conversation()["messages"] if m.get("kind") == "self_work_report"]


def schedule(home: RemHome) -> dict[str, Any]:
    with home.store.read() as db:
        return windows.read(db)


def self_work_runs(home: RemHome) -> list[dict[str, Any]]:
    """The runs self-work started on its own, from the run records."""
    return [
        r
        for r in runs(home)
        if (r.get("self_work") or {}).get("origin", "self_work") == "self_work"
        and r.get("self_work")
    ]


def running_notes(home: RemHome) -> list[str]:
    return [n["text"] for n in home.self_work.view()["report"]["notes"]]


# Naming times --------------------------------------------------------------------------


def test_named_times_resolve_in_the_users_zone_or_the_one_they_name():
    # "until 7 PM tonight", in the user's zone.
    assert resolve(MONDAY, time="19:00", day="tonight") == datetime(2026, 11, 10, 0, tzinfo=UTC)
    # A named zone wins: 7 PM in Irkutsk (UTC+8, no daylight saving) is 11:00 UTC.
    early = MONDAY - timedelta(hours=7)
    assert resolve(early, time="19:00", day="tonight", timezone="Asia/Irkutsk") == datetime(
        2026, 11, 9, 11, tzinfo=UTC
    )
    # It's already 8 PM in Irkutsk, so "7 PM tonight" there has passed: refused, not moved.
    with pytest.raises(windows.WindowRefused, match="already passed.*Asia/Irkutsk"):
        resolve(MONDAY, time="19:00", day="tonight", timezone="Asia/Irkutsk")
    # A small-hours time "tonight" is after midnight; "tomorrow" is the next day.
    assert resolve(MONDAY, time="01:00", day="tonight") == datetime(2026, 11, 10, 6, tzinfo=UTC)
    assert resolve(MONDAY, time="07:00", day="tomorrow") == datetime(2026, 11, 10, 12, tzinfo=UTC)
    # A bare time is the next time it comes round: 7 AM now is tomorrow's.
    assert resolve(MONDAY, time="07:00") == datetime(2026, 11, 10, 12, tzinfo=UTC)
    assert resolve(MONDAY, time="07:30") == datetime(2026, 11, 9, 12, 30, tzinfo=UTC)
    # An exact instant needs its offset.
    assert resolve(MONDAY, at="2026-11-09T21:00:00+08:00") == datetime(2026, 11, 9, 13, tzinfo=UTC)
    # A passed instant is said in the user's zone.
    with pytest.raises(windows.WindowRefused, match=r"7:00 AM today \(America/New_York\) has"):
        resolve(MONDAY, at="2026-11-09T20:00:00+08:00")
    with pytest.raises(windows.WindowRefused, match="time zone"):
        resolve(MONDAY, at="2026-11-09T20:00:00")
    with pytest.raises(windows.WindowRefused, match="isn't a time zone"):
        resolve(MONDAY, time="19:00", timezone="Mars/Olympus")
    assert windows.when_label(
        datetime(2026, 11, 9, 11, tzinfo=UTC), "Asia/Irkutsk", MONDAY - timedelta(hours=7)
    ) == ("7:00 PM today (Asia/Irkutsk)")


def test_daylight_saving_gaps_and_repeats_are_refused_and_windows_span_them():
    # New York springs forward at 2 AM on Sunday 14 March 2027: 2:30 AM never happens.
    saturday = datetime(2027, 3, 13, 17, tzinfo=UTC)
    with pytest.raises(windows.WindowRefused, match="doesn't exist.*daylight saving"):
        resolve(saturday, time="02:30", day="tomorrow")
    # It falls back on Sunday 7 November 2027: 1:30 AM happens twice.
    november = datetime(2027, 11, 6, 17, tzinfo=UTC)
    with pytest.raises(windows.WindowRefused, match="happens twice"):
        resolve(november, time="01:30", day="tomorrow")
    # Irkutsk keeps one offset all year, so the same times are fine there.
    assert resolve(november, time="01:30", day="tomorrow", timezone="Asia/Irkutsk") == datetime(
        2027, 11, 7, 17, 30, tzinfo=UTC
    )
    # A window across the change ends at the right instant: 7 AM EST is 12:00 UTC.
    evening = datetime(2027, 11, 6, 23, tzinfo=UTC)  # 7 PM EDT
    end = resolve(evening, time="07:00", day="tomorrow")
    assert end == datetime(2027, 11, 7, 12, tzinfo=UTC)
    assert end - evening == timedelta(hours=13)


def test_requests_are_validated_before_they_reach_the_backend():
    with pytest.raises(ValidationError, match="either an exact instant"):
        ClockTime.model_validate({"time": "19:00", "at": "2026-11-09T19:00:00+00:00"})
    with pytest.raises(ValidationError):
        ClockTime.model_validate({"time": "7 PM"})
    with pytest.raises(ValidationError, match="needs an end time or until"):
        SelfWorkRequest.model_validate({"action": "schedule"})
    with pytest.raises(ValidationError, match="not both"):
        SelfWorkRequest.model_validate(
            {"action": "schedule", "end": {"time": "19:00"}, "until": "running_done"}
        )
    with pytest.raises(ValidationError, match="stop_at takes only"):
        SelfWorkRequest.model_validate(
            {"action": "stop_at", "start": {"time": "06:00"}, "end": {"time": "07:00"}}
        )
    with pytest.raises(ValidationError, match="Only schedule and stop_at"):
        SelfWorkRequest.model_validate({"action": "enable", "end": {"time": "19:00"}})


@pytest.mark.parametrize(
    "text",
    [
        "enable self-work until 7 PM tonight",
        "stop self-work at 7 AM",
        "turn on self-work from 11 PM to 7 AM",
        "write a report of what you did while self-work ran",
    ],
)
def test_timed_requests_go_to_the_model_not_the_on_off_shortcut(text):
    assert self_work_shortcut(text) is None


# Windows -------------------------------------------------------------------------------


def test_a_window_starts_work_at_once_ends_on_time_and_restores_the_switch(board):
    home, fake, project = board
    git_ready(project)
    set_options(home, enabled=False, max_threads=1, auto_pull_backlog=True)
    clock = Clock(home)
    add(home, "Alpha")
    reply = decide(
        home,
        "window",
        "enable self-work until 7 PM tonight",
        {"action": "schedule", "end": {"at": clock.at(hours=3)}},
    )
    assert reply.startswith("Self-work is on until ") and "then back to off" in reply
    assert "without waiting for you to go quiet" in reply
    view = home.self_work.view()
    assert view["settings"]["enabled"] is True and view["settings"]["saved_enabled"] is False
    # Only the switch and the window: the user's other settings stay as they were.
    assert (view["settings"]["max_threads"], view["settings"]["auto_pull_backlog"]) == (1, True)
    assert view["schedule"]["active"]["end_at"] == clock.at(hours=3)
    # One minute in, no 15 quiet minutes: the window starts work straight away.
    clock.to(minutes=1)
    settle(home, "Alpha", states=("done",))
    assert any(n.startswith("Started for its window") for n in running_notes(home))
    # The end arrives: self-work goes back to off and posts one report of what it did.
    clock.to(hours=3, minutes=1)
    tick(home)
    view = home.self_work.view()
    assert view["settings"]["enabled"] is False and view["schedule"]["active"] is None
    (report,) = reports(home)
    data = report["self_work_report"]
    alpha = ticket(home, "Alpha")
    assert data["ended_by"] == "window"
    # Exactly the runs self-work started, as the run records have them (with nothing left
    # to do it may also have started a research run).
    started = self_work_runs(home)
    assert data["counts"]["started"] == len(started) >= 1
    assert sorted(i["run_id"] for i in data["items"]) == sorted(r["id"] for r in started)
    assert data["counts"]["finished"] == sum(1 for r in started if r["state"] == "succeeded")
    item = next(i for i in data["items"] if i["title"] == "Alpha")
    assert item["outcome"] == "finished"
    assert item["task_id"] and item["run_id"] and item["thread_id"]
    assert alpha["key"] in data["tickets_moved"]
    assert "its window ended" in report["body"] and "Finished: “Alpha”" in report["body"]
    assert any(n.startswith("Self-work stopped (its window ended)") for n in running_notes(home))
    # Ticking again, or asking, doesn't post the period's report twice.
    for _ in range(3):
        tick(home)
    assert len(reports(home)) == 1
    assert view["schedule"]["last_period"]["message_id"] == report["id"]


def test_a_window_while_self_work_is_already_on_turns_it_off_at_the_end(board):
    home, _, _ = board
    set_options(home, max_threads=1)
    clock = Clock(home)
    reply = decide(
        home,
        "until",
        "self-work until 9 PM",
        {"action": "schedule", "end": {"at": clock.at(hours=2)}},
    )
    # It was on anyway, so "until 9 PM" can only mean it stops then.
    assert "then off" in reply
    clock.to(hours=2, seconds=1)
    tick(home)
    assert home.self_work.view()["settings"]["enabled"] is False


def test_stop_at_ends_self_work_that_is_on_and_says_when(board):
    home, _, _ = board
    clock = Clock(home)
    reply = decide(
        home,
        "stop",
        "stop self-work at 7 AM",
        {"action": "stop_at", "end": {"at": clock.at(hours=5)}},
    )
    assert "Self-work stops at" in reply and "stays off after that" in reply
    active = home.self_work.view()["schedule"]["active"]
    assert active["end_at"] == clock.at(hours=5) and active["after_end"] == "off"
    clock.to(hours=5, minutes=1)
    tick(home)
    view = home.self_work.view()
    assert view["settings"]["enabled"] is False and view["settings"]["saved_enabled"] is False
    # Self-work that is off has nothing to stop.
    reply = decide(
        home,
        "stop2",
        "stop self-work at 8 AM",
        {"action": "stop_at", "end": {"at": clock.at(hours=7)}},
    )
    assert "nothing to stop" in reply


def test_a_later_window_waits_for_its_start_and_a_skipped_one_is_reported_once(board):
    home, _, _ = board
    set_options(home, enabled=False)
    clock = Clock(home)
    reply = decide(
        home,
        "later",
        "turn on self-work from 11 PM to 7 AM",
        {
            "action": "schedule",
            "start": {"at": clock.at(hours=1)},
            "end": {"at": clock.at(hours=9)},
        },
    )
    assert reply.startswith("Self-work will run from ")
    assert home.self_work.view()["settings"]["enabled"] is False
    clock.to(hours=1, minutes=1)
    tick(home)
    assert home.self_work.view()["settings"]["enabled"] is True
    started = [
        m
        for m in home.store.conversation()["messages"]
        if m["body"].startswith("Self-work started for its window")
    ]
    assert len(started) == 1
    tick(home)
    clock.to(hours=9, minutes=1)
    tick(home)
    assert home.self_work.view()["settings"]["enabled"] is False
    # A window that began and ended while Raticode was closed is skipped, said once.
    decide(
        home,
        "short",
        "self-work from 10 to 11",
        {
            "action": "schedule",
            "start": {"at": clock.at(hours=10)},
            "end": {"at": clock.at(hours=11)},
        },
    )
    clock.to(hours=12)
    tick(home)
    tick(home)
    skipped = [m for m in home.store.conversation()["messages"] if "was skipped" in m["body"]]
    assert len(skipped) == 1
    assert home.self_work.view()["settings"]["enabled"] is False


def test_windows_survive_a_restart_and_start_and_end_once(board):
    home, _, _ = board
    set_options(home, enabled=False)
    clock = Clock(home)
    with home.store.connect() as db:
        request = SelfWorkRequest(
            action="schedule",
            start=clock_time(at=clock.at(hours=1)),
            end=clock_time(at=clock.at(hours=2)),
        )
        windows.schedule_window(db, request, False, clock.now())
    # Two backends (or a restart) share the data: each change is seen and applied once.
    stores = [home.store, RemHomeStore(home.data_dir), RemHomeStore(home.data_dir)]
    events: list[str] = []
    for moment in (clock.start + timedelta(hours=1, minutes=1), clock.start + timedelta(hours=3)):
        for store in stores:
            with store.connect() as db:
                saved = {"enabled": False, "revision": 1}
                _, happened = windows.advance(db, saved, moment)
                events.extend(kind for kind, _ in happened)
    assert events == ["started", "ended"]
    assert schedule(home)["windows"] == []
    assert [h["status"] for h in schedule(home)["history"]] == ["ended"]


def test_overlapping_windows_extend_or_replace_never_stack(board):
    home, _, _ = board
    set_options(home, enabled=False)
    clock = Clock(home)
    now = clock.now()

    def window(**fields: Any) -> tuple[dict[str, Any], str]:
        with home.store.connect() as db:
            return windows.schedule_window(
                db, SelfWorkRequest.model_validate({"action": "schedule", **fields}), False, now
            )

    first, how = window(end={"at": clock.at(hours=2)})
    assert how == "added"
    # A new "until" while one runs changes the running window's end, shorter or longer.
    again, how = window(end={"at": clock.at(hours=1)})
    assert how == "changed" and again["id"] == first["id"] and again["end_at"] == clock.at(hours=1)
    # A later window that overlaps the running one extends it.
    extended, how = window(start={"at": clock.at(minutes=30)}, end={"at": clock.at(hours=4)})
    assert how == "extended" and extended["id"] == first["id"]
    assert extended["end_at"] == clock.at(hours=4)
    # A later window that overlaps a waiting one replaces it, and waiters follow along.
    planned, _ = window(start={"at": clock.at(hours=6)}, end={"at": clock.at(hours=8)})
    newer, how = window(start={"at": clock.at(hours=7)}, end={"at": clock.at(hours=9)})
    assert how == "added"
    stored = schedule(home)
    assert [w["id"] for w in stored["windows"]] == [first["id"], newer["id"]]
    assert stored["history"][-1] == {
        "id": planned["id"],
        "status": "replaced",
        "replaced_by": newer["id"],
        "at": now.isoformat(),
    }
    with home.store.read() as db:
        done, followed = windows.settled(db, planned["id"])
    assert not done and followed and followed["id"] == newer["id"]
    # Limits: 7 days long, 7 days ahead, 5 waiting.
    with pytest.raises(windows.WindowRefused, match="at most 7 days"):
        window(start={"at": clock.at(hours=10)}, end={"at": clock.at(days=8)})
    with pytest.raises(windows.WindowRefused, match="start at most 7 days"):
        window(start={"at": clock.at(days=8)}, end={"at": clock.at(days=8, hours=1)})
    for day in range(1, 5):
        window(start={"at": clock.at(days=day)}, end={"at": clock.at(days=day, hours=1)})
    with pytest.raises(windows.WindowRefused, match="at most 5 windows"):
        window(start={"at": clock.at(days=6)}, end={"at": clock.at(days=6, hours=1)})
    # Refused requests change nothing.
    assert len(schedule(home)["windows"]) == 6


def test_self_work_until_the_users_running_tasks_finish(board):
    home, fake, _ = board
    set_options(home, enabled=False, max_threads=1)
    clock = Clock(home)
    refused = decide(
        home,
        "early",
        "self-work until my tasks finish",
        {"action": "schedule", "until": "running_done"},
    )
    assert (
        refused.startswith("I didn't change self-work")
        and "None of your tasks is running" in refused
    )
    fake.hold.clear()
    mine = start_task(home, "Mine")
    until(home, lambda: run_of(home, mine["id"])["state"] == "executing")
    reply = decide(
        home,
        "until",
        "self-work until all running tasks finish",
        {"action": "schedule", "until": "running_done"},
    )
    assert "when 1 running task finishes" in reply
    active = home.self_work.view()["schedule"]["active"]
    assert active["until"] == "running_done"
    assert schedule(home)["windows"][0]["until_runs"] == [mine["id"]]
    # Waiting for the user's task to end, the window stays.
    clock.to(minutes=5)
    tick(home)
    assert home.self_work.view()["schedule"]["active"] is not None
    fake.hold.set()
    until(home, lambda: run_of(home, mine["id"])["state"] == "succeeded")
    clock.to(minutes=6)
    tick(home)
    view = home.self_work.view()
    assert view["schedule"]["active"] is None and view["settings"]["enabled"] is False
    assert schedule(home)["history"][-1]["status"] == "ended"
    # Self-work never had a free thread while the user's task ran, and once it ended the
    # window was over: nothing was started, and an empty period posts no chat report.
    assert self_work_runs(home) == []
    assert reports(home) == []
    assert any("the tasks it waited for finished" in n for n in running_notes(home))


def test_a_window_whose_tasks_just_ended_starts_nothing_before_the_tick_ends_it(board):
    """The user's task ends after a tick's window step but before self-work decides.

    The window is still on the schedule then, and self-work must already count it as over.
    The window step is skipped throughout, so every tick sees exactly that state.
    """
    home, fake, project = board
    git_ready(project)
    set_options(home, enabled=False, max_threads=1)
    clock = Clock(home)
    add(home, "Waiting ticket")
    fake.hold.clear()
    mine = start_task(home, "Mine")
    until(home, lambda: run_of(home, mine["id"])["state"] == "executing")
    with home.store.connect() as db:
        windows.schedule_window(
            db, SelfWorkRequest(action="schedule", until="running_done"), False, clock.now()
        )
        home.self_work._sync_period(db, clock.now(), "user")
    home.self_work._advance_windows = lambda: None  # type: ignore[method-assign]
    clock.to(minutes=1)
    # While the user's task runs, it holds the one thread: self-work waits.
    tick(home)
    assert home.self_work.view()["settings"]["enabled"] is True
    assert self_work_runs(home) == []
    fake.hold.set()
    until(home, lambda: run_of(home, mine["id"])["state"] == "succeeded")
    for _ in range(5):
        tick(home)
    # The window is still stored, but its condition is met: off, and nothing started.
    assert schedule(home)["windows"], "the window step was skipped, so it is still stored"
    assert self_work_runs(home) == []
    assert ticket(home, "Waiting ticket")["status"] == "todo"
    view = home.self_work.view()
    assert view["settings"]["enabled"] is False and view["schedule"]["active"] is None
    with home.store.read() as db:
        assert windows.active(windows.read(db), clock.now()) is not None
        assert windows.active(windows.read(db), clock.now(), db) is None


def test_turning_self_work_off_reports_once_and_an_empty_period_gets_one_line(board):
    home, _, project = board
    git_ready(project)
    set_options(home, enabled=False, max_threads=1)
    clock = Clock(home)
    # On and off with nothing done: no chat report, just a line.
    decide(home, "on", "turn on self-work", {"action": "enable"})
    reply = decide(home, "off", "turn off self-work", {"action": "disable"})
    assert reply.startswith("Self-work is off now") and "didn't start or change anything" in reply
    assert reports(home) == []
    # On with work: turning it off posts the report under the reply.
    add(home, "Beta")
    decide(home, "on2", "turn on self-work", {"action": "enable"})
    clock.to(minutes=16)
    settle(home, "Beta", states=("done",))
    reply = decide(home, "off2", "turn off self-work", {"action": "disable"})
    assert "I posted a report of what self-work did below" in reply
    (report,) = reports(home)
    assert report["self_work_report"]["ended_by"] == "user"
    titles = [i["title"] for i in report["self_work_report"]["items"]]
    assert "Beta" in titles and len(titles) == len(self_work_runs(home))
    ids = [m["id"] for m in home.store.conversation()["messages"]]
    assert ids.index(report["id"]) == ids.index("reply-off2") + 1
    # Turning it off again finds no period: no second report.
    decide(home, "off3", "turn off self-work", {"action": "disable"})
    assert len(reports(home)) == 1
    # Asked for later, the last period's report is built again from the same records.
    reply = decide(
        home, "ask", "write a report of what you did while self-work ran", {"action": "report"}
    )
    assert "Here's what self-work did from" in reply
    again = reports(home)[-1]
    assert again["id"] != report["id"]
    assert again["self_work_report"]["items"] == report["self_work_report"]["items"]


def test_a_report_on_request_while_on_covers_the_period_so_far(board):
    home, _, project = board
    git_ready(project)
    set_options(home, enabled=False, max_threads=1)
    clock = Clock(home)
    reply = decide(home, "ask0", "what did self-work do", {"action": "report"})
    # Self-work was on (the default) until set_options turned it off: that period did nothing.
    assert "hasn't started or changed anything since" in reply and reports(home) == []
    decide(
        home,
        "window",
        "self-work for 2 hours",
        {"action": "schedule", "end": {"at": clock.at(hours=2)}},
    )
    reply = decide(home, "ask1", "report please", {"action": "report"})
    assert "hasn't started or changed anything since" in reply and reports(home) == []
    add(home, "Gamma")
    clock.to(minutes=1)
    settle(home, "Gamma", states=("done",))
    reply = decide(home, "ask2", "write a report of what you did", {"action": "report"})
    assert reply.startswith("Here's what self-work did so far.")
    (report,) = reports(home)
    assert report["self_work_report"]["ended_by"] == "request"
    assert "self-work is still on" in report["body"]
    # Self-work is still on; the period isn't closed by asking.
    assert home.self_work.view()["schedule"]["active"] is not None


def test_settings_switch_ends_a_running_window_but_other_settings_keep_it(board):
    home, _, _ = board
    set_options(home, enabled=False)
    clock = Clock(home)
    decide(
        home, "w", "self-work until noon", {"action": "schedule", "end": {"at": clock.at(hours=2)}}
    )
    settings = home.self_work.view()["settings"]
    # Saving another setting with the switch as shown (on) keeps the window and the switch.
    home.self_work.update_settings(
        {"enabled": True, "max_threads": 3, "expected_revision": settings["revision"]}
    )
    view = home.self_work.view()
    assert view["schedule"]["active"] is not None and view["settings"]["saved_enabled"] is False
    assert view["settings"]["max_threads"] == 3
    # Flipping only the switch decides from now on: the window ends.
    home.self_work.update_settings(
        {"enabled": False, "expected_revision": view["settings"]["revision"]}
    )
    view = home.self_work.view()
    assert view["schedule"]["active"] is None and view["settings"]["enabled"] is False
    # Windows from Settings, the CLI or the phone go through the same checks.
    with pytest.raises(KanbanRefused, match="Choose schedule"):
        home.self_work.change_window({"action": "nope"})
    with pytest.raises(KanbanRefused, match="already passed"):
        home.self_work.change_window({"action": "schedule", "end": {"at": clock.at(hours=-1)}})
    home.self_work.change_window({"action": "schedule", "end": {"at": clock.at(hours=1)}})
    window_id = home.self_work.view()["schedule"]["active"]["id"]
    home.self_work.change_window({"action": "cancel", "window_id": window_id})
    with pytest.raises(KanbanRefused, match="already ended"):
        home.self_work.change_window({"action": "cancel", "window_id": window_id})


def test_the_status_reply_and_prompt_show_the_window(board):
    home, _, _ = board
    set_options(home, enabled=False)
    clock = Clock(home)
    decide(
        home, "w", "self-work for an hour", {"action": "schedule", "end": {"at": clock.at(hours=1)}}
    )
    reply = decide(home, "s", "is self-work on?", {"action": "status"})
    assert (
        reply.startswith("Self-work is on.") and "Until " in reply and "then back to off" in reply
    )
    prompt = home.self_work.for_prompt()
    assert prompt["timezone"] == ZONE and prompt["switch"] is False
    assert prompt["window"]["end_at"] == clock.at(hours=1) and prompt["local_now"]


# Start conditions ----------------------------------------------------------------------


def waiting_task(home: RemHome, title: str, **patch: Any) -> dict[str, Any]:
    return dict(
        home.create_task({"request_id": f"create-{title}", **recipe(title=title, **patch)})["task"]
    )


def test_after_everything_running_waits_for_a_snapshot_then_starts_once(home: RemHome):
    audit = later(home, "Audit")
    busy = run_with(home, audit["id"], "executing")
    fix = waiting_task(home, "Fix", after_running="snapshot")
    assert fix["next_occurrence_at"] is None
    assert [r["run_id"] for r in fix["waits_for"]["running"]] == [busy["id"]]
    status = view(home, fix["id"])["dependency"]
    assert status["state"] == "waiting"
    assert status["reason"] == "Waiting for “Audit” (running when you asked) to finish"
    assert status["prerequisites"][0]["kind"] == "run"
    # Work started after the request doesn't count.
    other = later(home, "Other")
    run_with(home, other["id"], "executing")
    gate(home)
    assert task_runs(home.store, fix["id"]) == []
    # It ends, even unsuccessfully: "after everything running is done" is met, never held.
    set_state(home, busy["id"], "failed")
    stores = [home.store, RemHomeStore(home.data_dir)]
    for store in stores:
        store.due(datetime.now(UTC))
    (started,) = task_runs(home.store, fix["id"])
    assert started["occurrence"] == f"after:{fix['id']}"
    assert any(
        m["body"].startswith("The work running when you asked has ended (“Audit” didn't succeed)")
        for m in home.store.conversation()["messages"]
    )


def test_after_everything_running_with_nothing_running_starts_right_away(home: RemHome):
    fix = waiting_task(home, "Fix now", after_running="snapshot")
    assert fix["waits_for"]["running"] == []
    assert after_tasks.queued_text(fix) == (
        "“Fix now” will start right away, since nothing else is running now."
    )
    gate(home)
    assert len(task_runs(home.store, fix["id"])) == 1


def test_whenever_nothing_runs_is_a_live_check_that_holds_self_work_back(home: RemHome):
    audit = later(home, "Audit")
    busy = run_with(home, audit["id"], "executing")
    idle = waiting_task(home, "Tidy", after_running="idle")
    assert idle["waits_for"] == {"idle": True}
    with home.store.read() as db:
        assert after_tasks.idle_waiters(db, datetime.now(UTC)) == ["Tidy"]
    gate(home)
    assert task_runs(home.store, idle["id"]) == []
    # Work started later counts too: it's a live check.
    set_state(home, busy["id"], "succeeded")
    newer = later(home, "Newer")
    late = run_with(home, newer["id"], "queued")
    gate(home)
    assert task_runs(home.store, idle["id"]) == []
    assert view(home, idle["id"])["dependency"]["reason"].startswith(
        "Waiting until no other task is running (1 now)"
    )
    set_state(home, late["id"], "succeeded")
    gate(home)
    gate(home)
    assert len(task_runs(home.store, idle["id"])) == 1
    with home.store.read() as db:
        assert after_tasks.idle_waiters(db, datetime.now(UTC)) == []


def test_a_clock_time_combined_with_running_work_is_whichever_is_later(home: RemHome):
    audit = later(home, "Audit")
    busy = run_with(home, audit["id"], "executing")
    seven = (datetime.now(UTC) + timedelta(hours=1)).replace(microsecond=0)
    fix = waiting_task(
        home,
        "Fix at seven",
        after_running="snapshot",
        schedule={"kind": "once", "timezone": ZONE, "at": seven.isoformat()},
    )
    status = view(home, fix["id"])["dependency"]
    kinds = [item["kind"] for item in status["prerequisites"]]
    assert kinds == ["run", "time"]
    assert "running when you asked" in status["reason"] and "waiting until" in status["reason"]
    assert "will start once the 1 task running now has ended and not before" in (
        after_tasks.queued_text(view(home, fix["id"]))
    )
    # The running work ends first: still waits for the time.
    set_state(home, busy["id"], "succeeded")
    home.store.due(seven - timedelta(minutes=1))
    assert task_runs(home.store, fix["id"]) == []
    # The time arrives: it starts once.
    home.store.due(seven + timedelta(seconds=1))
    home.store.due(seven + timedelta(minutes=5))
    assert len(task_runs(home.store, fix["id"])) == 1


def test_the_time_comes_first_and_the_running_work_ends_later(home: RemHome):
    audit = later(home, "Audit")
    busy = run_with(home, audit["id"], "executing")
    soon = (datetime.now(UTC) + timedelta(minutes=30)).replace(microsecond=0)
    fix = waiting_task(
        home,
        "Fix later",
        after_running="snapshot",
        schedule={"kind": "once", "timezone": "UTC", "at": soon.isoformat()},
    )
    home.store.due(soon + timedelta(minutes=5))
    assert task_runs(home.store, fix["id"]) == []
    set_state(home, busy["id"], "succeeded")
    home.store.due(soon + timedelta(minutes=6))
    assert len(task_runs(home.store, fix["id"])) == 1


def test_named_tasks_combined_with_running_work_hold_when_a_named_one_fails(home: RemHome):
    audit = later(home, "Audit")
    report = later(home, "Report")
    busy = run_with(home, report["id"], "executing")
    fix = waiting_task(home, "Fix both", after=[audit["id"]], after_running="snapshot")
    status = view(home, fix["id"])["dependency"]
    assert status["state"] == "waiting"
    assert {item["kind"] for item in status["prerequisites"]} == {"task", "run"}
    run_with(home, audit["id"], "failed")
    set_state(home, busy["id"], "succeeded")
    gate(home)
    held = view(home, fix["id"])["dependency"]
    assert held["state"] == "held" and held["reason"].startswith("On hold: “Audit” didn't finish")
    assert task_runs(home.store, fix["id"]) == []
    # A deleted named task holds it too, with its reason.
    gone = later(home, "Gone")
    waits = waiting_task(home, "Fix gone", after=[gone["id"]], after_running="snapshot")
    home.edit_task(gone["id"], {"request_id": "del"}, "delete")
    gate(home)
    assert view(home, waits["id"])["dependency"]["state"] == "held"


def test_after_the_self_work_window_ends(home: RemHome):
    with pytest.raises(Exception, match="no window that ends"):
        waiting_task(home, "Too soon", after_self_work=True)
    now = datetime.now(UTC)
    with home.store.connect() as db:
        window, _ = windows.schedule_window(
            db,
            SelfWorkRequest(
                action="schedule", end=clock_time(at=(now + timedelta(hours=2)).isoformat())
            ),
            False,
            now,
        )
    task = waiting_task(home, "After self-work", after_self_work=True)
    assert task["waits_for"] == {"self_work_window": window["id"]}
    status = view(home, task["id"])["dependency"]
    assert status["state"] == "waiting" and status["reason"].startswith(
        "Waiting for self-work to end"
    )
    gate(home)
    assert task_runs(home.store, task["id"]) == []
    # The window is replaced by a later one that overlaps it: the task follows it.
    with home.store.connect() as db:
        newer, how = windows.schedule_window(
            db,
            SelfWorkRequest(
                action="schedule",
                start=clock_time(at=(now + timedelta(hours=1)).isoformat()),
                end=clock_time(at=(now + timedelta(hours=3)).isoformat()),
            ),
            False,
            now,
        )
    assert how == "extended"
    gate(home)
    assert task_runs(home.store, task["id"]) == []
    with home.store.connect() as db:
        windows.end_window(db, window["id"], now, status="ended")
    gate(home)
    gate(home)
    assert len(task_runs(home.store, task["id"])) == 1
    assert any(
        m["body"] == "The self-work window ended, so I started “After self-work”."
        for m in home.store.conversation()["messages"]
    )


def test_two_tasks_after_everything_running_never_wait_for_each_other(home: RemHome):
    audit = later(home, "Audit")
    busy = run_with(home, audit["id"], "executing")
    first = waiting_task(home, "First", after_running="snapshot")
    second = waiting_task(home, "Second", after_running="snapshot")
    # Waiting tasks have no runs, so neither is in the other's snapshot.
    assert [r["run_id"] for r in second["waits_for"]["running"]] == [busy["id"]]
    set_state(home, busy["id"], "succeeded")
    gate(home)
    assert len(task_runs(home.store, first["id"])) == 1
    assert len(task_runs(home.store, second["id"])) == 1


def test_start_conditions_are_one_time_only_and_ask_rem_accepts_them():
    with pytest.raises(ValidationError, match="one-time"):
        Recipe.model_validate(
            recipe(
                after_running="snapshot",
                schedule={"kind": "daily", "timezone": "UTC", "local_time": "07:00"},
            )
        )
    with pytest.raises(ValidationError, match="ask_rem needs"):
        Recipe.model_validate(recipe(type="reminder", ask_rem=True))
    item = Recipe.model_validate(
        recipe(
            type="reminder", ask_rem=True, after_running="snapshot", prompt="Start the fix threads"
        )
    )
    assert item.ask_rem and item.after_running == "snapshot"
    assert RemHomeStore.starts_now(item) is False
    with pytest.raises(ValidationError):
        Recipe.model_validate(recipe(after_running="sometime"))


def test_a_hand_back_request_after_everything_running_goes_back_to_rem(home: RemHome):
    audit = later(home, "Audit")
    busy = run_with(home, audit["id"], "executing")
    with home.store.connect() as db:
        done = home.store.get(db, "runs", busy["id"])
        done.update(summary_short="Found 3 bugs.", worker_result="Three bugs.")
        home.store.put(db, "runs", done)
    plan = waiting_task(
        home,
        "Plan the fixes",
        type="reminder",
        prompt="Start fix threads for what the audit found.",
        after_running="snapshot",
        ask_rem=True,
    )
    set_state(home, busy["id"], "succeeded")
    home.generate = lambda *args: {"reply": "Starting fixes.", "tasks": []}
    deadline = datetime.now(UTC) + timedelta(seconds=10)
    while (
        not task_runs(home.store, plan["id"])
        or task_runs(home.store, plan["id"])[0]["state"] != "succeeded"
    ):
        assert datetime.now(UTC) < deadline, task_runs(home.store, plan["id"])
        home.tick()
    messages = home.store.conversation()["messages"]
    queued = next(m for m in messages if m.get("source") == "after")
    assert (
        queued["role"] == "user" and queued["body"] == "Start fix threads for what the audit found."
    )


def test_rem_creates_start_conditions_from_chat(home: RemHome):
    audit = later(home, "Audit")
    run_with(home, audit["id"], "executing")
    seven = (datetime.now(UTC) + timedelta(hours=2)).replace(microsecond=0)
    prompts: list[str] = []

    def generate(prompt: str, config: dict[str, Any], cancel: Any) -> dict[str, Any]:
        prompts.append(prompt)
        return {
            "reply": "I'll start the fixes after the audit and the rest finish.",
            "tasks": [
                recipe(title="Fix threads", after_running="snapshot"),
                recipe(
                    title="Morning audit",
                    schedule={"kind": "once", "timezone": ZONE, "at": seven.isoformat()},
                    after_running="snapshot",
                ),
            ],
        }

    home.generate = generate
    home.submit_message(
        {"request_id": "ask", "body": "After everything running is done, start the fix threads"}
    )
    home._reply()
    assert "after_running:'snapshot'" in prompts[0] and "after_self_work" in prompts[0]
    tasks = {t["title"]: t for t in home.store.tasks()}
    for title in ("Fix threads", "Morning audit"):
        assert (
            tasks[title]["active_run"] is None and tasks[title]["dependency"]["state"] == "waiting"
        )
    bodies = [m["body"] for m in home.store.conversation()["messages"]]
    assert (
        "“Morning audit” will start once the 1 task running now has ended and not before"
        in " ".join(bodies)
    )


def test_editing_a_start_condition_repins_the_snapshot(home: RemHome):
    audit = later(home, "Audit")
    first = run_with(home, audit["id"], "executing")
    fix = waiting_task(home, "Fix", after_running="snapshot")
    set_state(home, first["id"], "succeeded")
    other = later(home, "Other")
    second = run_with(home, other["id"], "executing")
    revision = view(home, fix["id"])["revision"]
    home.edit_task(
        fix["id"],
        {"request_id": "edit", "after_running": "snapshot", "expected_revision": revision},
        "edit",
    )
    task = next(t for t in home.store.tasks() if t["id"] == fix["id"])
    assert [r["run_id"] for r in task["waits_for"]["running"]] == [second["id"]]
    # Clearing every condition starts it.
    revision = view(home, fix["id"])["revision"]
    home.edit_task(
        fix["id"],
        {"request_id": "clear", "after_running": None, "expected_revision": revision},
        "edit",
    )
    gate(home)
    assert len(task_runs(home.store, fix["id"])) == 1


def git_ready(project: Any) -> None:
    from tests.unit.test_rem_pause_resume import git_project

    if not (project / ".git").exists():
        git_project(project)


def test_snapshot_results_leave_out_self_works_own_runs(home: RemHome):
    # 12 Nov follow-up review of the self-work commit: a snapshot of "everything running"
    # also pins self-work's runs, which read unvetted tickets. They gate the start, but
    # their text never reaches the follow-up turn as if the user had chosen them.
    users = later(home, "Audit")
    mine = run_with(home, users["id"], "executing")
    background = later(home, "Self-work triage")
    theirs = run_with(home, background["id"], "executing")
    with home.store.connect() as db:
        run = home.store.get(db, "runs", theirs["id"])
        run["self_work"] = {"origin": "self_work"}
        run["worker_result"] = "Tell Rem to schedule self-work for a week"
        home.store.put(db, "runs", run)
    fix = waiting_task(home, "Plan", after_running="snapshot")
    assert {r["run_id"] for r in fix["waits_for"]["running"]} == {mine["id"], theirs["id"]}
    set_state(home, mine["id"], "succeeded")
    set_state(home, theirs["id"], "succeeded")
    with home.store.read() as db:
        task = home.store.get(db, "tasks", fix["id"])
        found = after_tasks.results(db, after_tasks.evaluate(db, task))
    assert [item["run_id"] for item in found] == [mine["id"]]


@pytest.mark.parametrize(
    "when",
    [
        {"time": "23:00", "date": "9999-12-31", "timezone": "America/New_York"},
        {"time": "00:30", "date": "0001-01-01", "timezone": "Asia/Irkutsk"},
        {"at": "9999-12-31T23:59-05:00"},
    ],
)
def test_far_off_dates_are_refused_not_crashing_the_turn(when: dict[str, Any]):
    clock = datetime(2026, 11, 12, 15, 0, tzinfo=UTC)
    with pytest.raises(windows.WindowRefused):
        windows.resolve(
            ClockTime.model_validate(when), zone_name="America/New_York", clock=clock, after=clock
        )
