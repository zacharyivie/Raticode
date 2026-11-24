"""When self-work stops: the user's own condition ("done when") and a latest time.

Covers setting both from one chat sentence, Settings and the HTTP route; refusing times that
can't be used; Rem's model judging the condition against real records (met, not met, a failed
call, "met" without evidence); the latest time stopping self-work regardless; whichever comes
first winning, once, with one report; restarts; edits and clears; and Rem never loosening a
stop the user set. Fake providers and isolated data only (the ``board`` fixture). The user's
time zone is pinned to America/New_York.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from gofer.ui import rem_self_work_stop as stops
from gofer.ui import rem_self_work_windows as windows
from gofer.ui.headless import agent_request_refusal
from gofer.ui.rem_home import SELF_WORK_RULES, RemHome
from gofer.ui.rem_home_models import ClockTime, SelfWorkRequest
from gofer.ui.rem_kanban import KanbanRefused
from gofer.ui.rem_self_work import SelfWork, quoted
from tests.unit.test_cli_rem_api import app, cli, runner
from tests.unit.test_cli_rem_api import backend as backend
from tests.unit.test_rem_kanban import add, api_server, set_options, tick, ticket, wait_for
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_rem_pause_resume import reply_to
from tests.unit.test_rem_self_work_windows import Clock, git_ready, reports, self_work_runs

assert api_server  # a fixture, imported for the HTTP test below

ZONE = "America/New_York"
JUDGE = stops.JUDGE_RULES[:60]


@pytest.fixture(autouse=True)
def user_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(windows, "default_zone", lambda: ZONE)


class Model:
    """Rem's model: chat turns return ``self_work``; stop checks answer with ``judge``.

    ``judge`` gets the condition and the records the check was shown, and returns the
    model's JSON (or raises, for an outage). Every check's records are kept in ``checks``.
    """

    def __init__(self, home: RemHome, judge: Any = None) -> None:
        self.home = home
        self.self_work: dict[str, Any] | None = None
        self.judge = judge or (lambda condition, found: {"met": False, "evidence": "Not yet."})
        self.checks: list[dict[str, Any]] = []
        home.generate = self.generate

    def generate(self, prompt: str, config: Any, cancel: Any) -> dict[str, Any]:
        if prompt.startswith(JUDGE):
            body = prompt.split("Stop condition, in the user's words: ", 1)[1]
            condition_line, records = body.split("\nRecords:\n", 1)
            found = json.loads(records)
            self.checks.append(found)
            return dict(self.judge(json.loads(condition_line), found))
        return {"reply": "Sure.", "tasks": [], "memories": [], "self_work": self.self_work}

    def say(self, request: str, body: str, self_work: dict[str, Any] | None) -> str:
        self.self_work = self_work
        try:
            return reply_to(self.home, request, body)
        finally:
            self.self_work = None


def judged(home: RemHome) -> None:
    """Let a stop check running now finish."""
    thread = home.self_work._judge
    if thread is not None:
        thread.join(timeout=5)


def step(home: RemHome) -> bool:
    """One pass of Rem's loop, letting a stop check it started finish, then one more.

    Returns False so conditions can step while they wait."""
    tick(home)
    judged(home)
    tick(home)
    return False


def stop_view(home: RemHome) -> dict[str, Any]:
    return dict(home.self_work.view()["stop"])


def record(home: RemHome) -> dict[str, Any]:
    with home.store.read() as db:
        return stops.read(db)


def key(home: RemHome, title: str) -> str:
    return str(ticket(home, title)["key"])


@pytest.fixture(autouse=True)
def settle_across_the_moved_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs record their real finish time while these tests move self-work's clock ahead.

    Without this, a run seems to have ended long before the clock, so the check skips its
    short wait for the board to move the run's ticket, and on a busy machine it can judge
    the records while the ticket still shows the run working.
    """
    monkeypatch.setattr(stops, "SETTLE_WAIT", timedelta(days=2))


def done_judge(condition: str, found: dict[str, Any]) -> dict[str, Any]:
    """A careful judge: met only when every ticket the condition names is done."""
    named = found.get("named_tickets") or []
    open_ = [t["key"] for t in named if t.get("status") != "done"]
    if named and not open_:
        return {"met": True, "evidence": f"{', '.join(t['key'] for t in named)} are done."}
    return {"met": False, "evidence": f"{', '.join(open_) or 'Nothing'} still open."}


# Reading the condition and the time --------------------------------------------------------


def test_ticket_ranges_are_written_out_for_the_judge():
    assert stops.ticket_numbers("Stop when REM-205 to REM-208 are done") == [205, 206, 207, 208]
    assert stops.ticket_numbers("rem-3 through 5, and REM-9") == [3, 4, 5, 9]
    assert stops.ticket_numbers("REM-1..REM-2 and REM-1") == [1, 2]
    # A range too long to be meant is not expanded, but its ends are still named.
    assert stops.ticket_numbers("REM-1 to REM-900") == [1, 900]
    assert stops.ticket_numbers("when the audit is finished") == []


def test_latest_times_follow_the_window_rules():
    clock = datetime(2026, 10, 30, 12, tzinfo=UTC)
    # 1:30 AM on 1 Nov 2026 happens twice in New York: asked about, never guessed.
    with pytest.raises(stops.StopRefused, match="happens twice"):
        stops.resolve_fallback(ClockTime(time="01:30", date="2026-11-01", timezone=ZONE), clock)
    with pytest.raises(stops.StopRefused, match="already passed"):
        stops.resolve_fallback(ClockTime(time="07:00", date="2026-10-29"), clock)
    with pytest.raises(stops.StopRefused, match="at most 7 days"):
        stops.resolve_fallback(ClockTime(time="07:00", date="2026-11-08"), clock)
    # "NLT 12AM": the next midnight in the user's zone (8 AM here is 12:00 UTC).
    end, place = stops.resolve_fallback(ClockTime(time="00:00"), clock)
    assert place == ZONE and end == datetime(2026, 10, 31, 4, tzinfo=UTC)


def test_the_chat_request_carries_both_and_rules_name_them():
    request = SelfWorkRequest.model_validate(
        {
            "action": "stop_when",
            "done_when": "REM-205 to REM-212 are done",
            "no_later_than": {"time": "00:00"},
            "user_words": "Stop when REM-205 to REM-212 are complete, but NLT 12AM",
        }
    )
    assert request.no_later_than == ClockTime(time="00:00")
    with pytest.raises(ValueError):
        SelfWorkRequest.model_validate({"action": "stop_when"})
    with pytest.raises(ValueError):
        SelfWorkRequest.model_validate({"action": "disable", "done_when": "x"})
    with pytest.raises(ValueError):
        SelfWorkRequest.model_validate({"action": "status", "clear": "both"})
    # Enabling with a condition is one request.
    SelfWorkRequest.model_validate({"action": "enable", "done_when": "REM-1 is done"})
    assert "stop_when" in SELF_WORK_RULES and "NLT 12AM" in SELF_WORK_RULES
    assert "user_words" in SELF_WORK_RULES and "never compute instants" in SELF_WORK_RULES


def test_quotes_are_checked_against_what_the_user_said():
    said = "Actually, drop the stop condition — keep going until midnight."
    assert quoted("drop the stop condition", said)
    assert quoted("Drop the STOP condition!", said)
    assert not quoted("clear it", said)
    assert not quoted("", said)


# From chat -----------------------------------------------------------------------------


def test_one_sentence_sets_the_condition_and_the_latest_time(board):
    home, _, _ = board
    clock = Clock(home)
    model = Model(home)
    sentence = "Stop when REM-5 and REM-6 are complete, but NLT 12AM"
    reply = model.say(
        "both",
        sentence,
        {
            "action": "stop_when",
            "done_when": "REM-5 and REM-6 are complete",
            "no_later_than": {"time": "00:00"},
            "user_words": sentence,
        },
    )
    shown = stop_view(home)
    assert shown["active"] and shown["condition"] == "REM-5 and REM-6 are complete"
    midnight = windows.resolve(
        ClockTime(time="00:00"), zone_name=ZONE, clock=clock.now(), after=clock.now()
    )
    assert shown["no_later_than"] == midnight.isoformat()
    assert "Stops when “REM-5 and REM-6 are complete”, or at 12:00 AM" in reply
    assert shown["no_later_than_label"] in reply
    # Status replies say it too, with the last check once there is one.
    status = model.say("status", "is self-work on?", {"action": "status"})
    assert "Stops when “REM-5 and REM-6 are complete”" in status


def test_a_time_that_cant_be_used_changes_nothing(board):
    home, _, _ = board
    set_options(home, enabled=False)
    Clock(home)
    model = Model(home)
    reply = model.say(
        "past",
        "turn on self-work until REM-1 is done, NLT yesterday 7 AM",
        {
            "action": "enable",
            "done_when": "REM-1 is done",
            "no_later_than": {"time": "07:00", "date": "2020-01-01"},
            "user_words": "turn on self-work until REM-1 is done",
        },
    )
    assert reply.startswith("I didn't change self-work") and "already passed" in reply
    assert home.self_work.view()["settings"]["enabled"] is False
    assert not stop_view(home)["active"]
    # With self-work off, a stop alone has nothing to stop.
    reply = model.say(
        "off", "stop when REM-1 is done", {"action": "stop_when", "done_when": "REM-1 is done"}
    )
    assert "Self-work is off, so there's nothing to stop" in reply
    assert not stop_view(home)["active"]
    # Turning it on with a condition sets both in one go.
    reply = model.say(
        "on",
        "turn on self-work until REM-1 is done",
        {"action": "enable", "done_when": "REM-1 is done"},
    )
    assert home.self_work.view()["settings"]["enabled"] is True
    assert stop_view(home)["condition"] == "REM-1 is done"
    assert "Stops when “REM-1 is done”" in reply


def test_rem_cant_loosen_a_stop_the_user_set(board):
    home, _, _ = board
    clock = Clock(home)
    # Times a few hours ahead, so each one's next occurrence keeps this order whatever
    # the time of day the test runs (a fixed 10 PM would mean tomorrow after 10 PM).
    hour = (clock.now().astimezone(ZoneInfo(ZONE)) + timedelta(hours=3)).replace(
        minute=0, second=0, microsecond=0
    )

    def at(moment: datetime) -> tuple[str, str]:
        return f"{moment:%H:%M}", f"{moment:%I:%M %p}".lstrip("0")

    (set_time, set_words), (later, later_words) = at(hour), at(hour + timedelta(minutes=30))
    earlier, earlier_words = at(hour - timedelta(hours=1))
    model = Model(home)
    model.say(
        "set",
        f"stop when REM-1 is done, no later than {set_words}",
        {
            "action": "stop_when",
            "done_when": "REM-1 is done",
            "no_later_than": {"time": set_time},
        },
    )
    before = record(home)
    # The model reads "how's it going?" as a reason to drop the condition: refused.
    reply = model.say(
        "drift",
        "how's it going?",
        {"action": "clear_stop", "clear": "both", "user_words": "drop the condition"},
    )
    assert "I left when self-work stops as you set it" in reply
    reply = model.say(
        "later",
        "keep it up",
        {"action": "stop_when", "no_later_than": {"time": later}, "user_words": "move it later"},
    )
    assert "I left when self-work stops as you set it" in reply
    assert record(home)["no_later_than"] == before["no_later_than"]
    # The user asking, in words found in their message, moves it later.
    model.say(
        "later2",
        f"Please move the stop to {later_words}",
        {
            "action": "stop_when",
            "no_later_than": {"time": later},
            "user_words": f"move the stop to {later_words}",
        },
    )
    assert record(home)["no_later_than"] > before["no_later_than"]
    reply = model.say(
        "change",
        "what's next?",
        {"action": "stop_when", "done_when": "REM-1 or REM-2 is done", "user_words": ""},
    )
    assert "I left when self-work stops as you set it" in reply
    assert record(home)["condition"] == "REM-1 is done"
    # Tightening needs no quote: an earlier time.
    model.say(
        "earlier",
        f"make it {earlier_words}",
        {"action": "stop_when", "no_later_than": {"time": earlier}},
    )
    assert record(home)["no_later_than"] < before["no_later_than"]
    # Nor can a workflow or a task, even one allowed to act: only the user's own message.
    with home.store.connect() as db:
        said = home.self_work.apply_chat(
            db,
            SelfWorkRequest(action="clear_stop", clear="both", user_words="clear it"),
            from_user=False,
            said="clear it",
        )
    assert said.startswith("I didn't change self-work: only you can")
    assert stops.active(record(home))
    # The user, in their own words, can.
    reply = model.say(
        "clear",
        "clear the stop condition and the time",
        {"action": "clear_stop", "clear": "both", "user_words": "clear the stop condition"},
    )
    assert "Cleared when self-work stops" in reply and not stop_view(home)["active"]


# Judging the condition ------------------------------------------------------------------


def start_session(home: RemHome, clock: Clock, model: Model, condition: str, **extra: Any) -> None:
    set_options(home, enabled=True, max_threads=1)
    model.say(
        "stop",
        f"stop self-work when {condition}",
        {"action": "stop_when", "done_when": condition, "user_words": condition, **extra},
    )


def test_condition_met_stops_self_work_once_with_evidence_before_the_latest_time(board):
    home, _, project = board
    git_ready(project)
    clock = Clock(home)
    model = Model(home, done_judge)
    add(home, "Alpha", status="todo", priority="high")
    add(home, "Beta", status="todo", priority="low")
    alpha = key(home, "Alpha")
    start_session(home, clock, model, f"{alpha} is done", no_later_than={"at": clock.at(hours=6)})
    # A new condition is checked once straight away: not met, and nothing waits on it.
    step(home)
    assert stop_view(home)["judgment"]["met"] is False
    assert len(model.checks) == 1 and model.checks[0]["named_tickets"][0]["key"] == alpha
    clock.to(minutes=16)
    wait_for(lambda: not step(home) and not stop_view(home)["active"], 10)
    view = home.self_work.view()
    assert view["settings"]["enabled"] is False
    last = view["stop"]["last"]
    assert last["judgment"]["met"] is True and last["stopped_by"] == "done_when"
    assert f"{alpha} are done" in last["judgment"]["evidence"]
    # Beta never started: the check ran before the freed thread could take it.
    assert ticket(home, "Alpha")["status"] == "done"
    assert ticket(home, "Beta")["status"] == "todo"
    assert [r["recipe"]["title"] for r in self_work_runs(home)] == ["Alpha"]
    (report,) = reports(home)
    assert report["self_work_report"]["ended_by"] == "done_when"
    assert report["self_work_report"]["stop"]["met"] is True
    assert "its stop condition was met" in report["body"]
    assert f"Stop condition met: “{alpha} is done”. Evidence: {alpha} are done." in report["body"]
    # One check for the new condition, one for the finished task: no more.
    assert len(model.checks) == 2
    # The latest time passing later stops nothing again and posts no second report.
    clock.to(hours=30)
    step(home)
    step(home)
    assert len(reports(home)) == 1 and len(model.checks) == 2


def test_condition_not_met_keeps_working_and_checks_once_per_finished_task(board):
    home, _, project = board
    git_ready(project)
    clock = Clock(home)
    model = Model(home)  # never met
    add(home, "Alpha", status="todo", priority="high")
    add(home, "Beta", status="todo", priority="low")
    start_session(home, clock, model, "the release is published")
    step(home)
    clock.to(minutes=16)
    wait_for(
        lambda: (
            not step(home)
            and ticket(home, "Alpha")["status"] == "done"
            and ticket(home, "Beta")["status"] == "done"
        ),
        12,
    )
    step(home)
    shown = stop_view(home)
    assert shown["active"] and shown["judgment"]["met"] is False
    assert shown["judgment"]["evidence"] == "Not yet."
    assert home.self_work.view()["settings"]["enabled"] is True
    finished = [r for r in self_work_runs(home) if r["state"] == "succeeded"]
    # One for the new condition, then at most one per finished task (two finishing together
    # share one), never more.
    assert 2 <= len(model.checks) <= 1 + len(finished)
    checks = len(model.checks)
    # Within IDLE_EVERY of the last check, idle ticks don't ask again.
    step(home)
    step(home)
    assert len(model.checks) == checks
    # After it, an idle tick asks only when the records it reads have changed.
    with home.store.read() as db:
        stored = stops.read(db)
        later = clock.now() + stops.IDLE_EVERY + timedelta(minutes=1)
        stored["judgment"]["fingerprint"] = stops.fingerprint(stops.records(db, stored, later))
        assert stops.due(stored, db, later) is None
        assert stops.due(stored, db, later, idle=False) is None
        stored["judgment"]["fingerprint"] = "records changed"
        assert stops.due(stored, db, later) == ("idle", [])
        assert stops.due(stored, db, clock.now()) is None
    # Without ticket keys, the judge sees what changed and the board's counts.
    assert "board_counts" in model.checks[-1]
    assert model.checks[-1]["self_work_tasks_since_set"]
    assert reports(home) == []


def test_a_failed_or_unsupported_judgment_never_stops_self_work(board):
    home, _, _ = board
    clock = Clock(home)
    calls: list[str] = []

    def outage(condition: str, found: dict[str, Any]) -> dict[str, Any]:
        calls.append(condition)
        raise ValueError("provider down")

    model = Model(home, outage)
    start_session(home, clock, model, "REM-1 is done")
    step(home)
    assert calls and stop_view(home)["judge_failed"] is True
    assert home.self_work.view()["settings"]["enabled"] is True
    # Not again until RETRY_AFTER, and nothing is held meanwhile.
    step(home)
    assert len(calls) == 1 and not home.self_work.judging()
    clock.to(minutes=11)
    # "Met" with no evidence from the records isn't taken as met.
    model.judge = lambda condition, found: {"met": True, "evidence": "  "}
    step(home)
    shown = stop_view(home)
    assert shown["active"] and shown["judgment"]["met"] is False and not shown["judge_failed"]
    assert home.self_work.view()["settings"]["enabled"] is True


# The latest time ------------------------------------------------------------------------


def test_the_latest_time_stops_self_work_when_the_condition_never_comes(board):
    home, _, _ = board
    clock = Clock(home)
    model = Model(home)
    start_session(
        home,
        clock,
        model,
        "REM-99 is done",
        no_later_than={"at": (clock.now() + timedelta(minutes=30)).isoformat()},
    )
    step(home)
    clock.to(minutes=29)
    step(home)
    assert home.self_work.view()["settings"]["enabled"] is True
    clock.to(minutes=31)
    step(home)
    view = home.self_work.view()
    assert view["settings"]["enabled"] is False
    assert view["stop"]["last"]["stopped_by"] == "no_later_than"
    (report,) = reports(home)
    assert report["self_work_report"]["ended_by"] == "no_later_than"
    assert "it reached the latest stop time you set" in report["body"]
    assert "Stop condition not met yet: “REM-99 is done”" in report["body"]
    step(home)
    assert len(reports(home)) == 1


def test_a_latest_time_alone_ends_self_work_like_an_end_time(board):
    home, _, _ = board
    clock = Clock(home)
    model = Model(home)
    set_options(home, enabled=True)
    model.say(
        "nlt",
        "keep going but stop no later than 30 minutes from now",
        {"action": "stop_when", "no_later_than": {"at": clock.at(minutes=30)}},
    )
    shown = stop_view(home)
    assert shown["condition"] == "" and shown["label"].startswith("Stops at ")
    step(home)
    assert model.checks == []  # nothing to judge
    clock.to(minutes=31)
    step(home)
    assert home.self_work.view()["settings"]["enabled"] is False
    assert stop_view(home)["last"]["stopped_by"] == "no_later_than"


def test_the_stop_and_its_latest_time_survive_a_restart(board):
    home, _, _ = board
    clock = Clock(home)
    model = Model(home)
    start_session(home, clock, model, "REM-7 is done", no_later_than={"at": clock.at(minutes=40)})
    # A check that was running when Raticode quit doesn't block the next one.
    with home.store.connect() as db:
        stored = stops.read(db)
        stored["attempt"] = {"state": "running", "at": clock.now().isoformat()}
        stops.write(db, stored)
    home.self_work.close()
    restarted = SelfWork(home)
    restarted.clock = clock.now
    restarted.started_at = clock.start
    home.self_work = restarted
    step(home)
    assert len(model.checks) == 1 and stop_view(home)["judgment"]["met"] is False
    clock.to(minutes=41)
    step(home)
    assert home.self_work.view()["settings"]["enabled"] is False
    assert stop_view(home)["last"]["stopped_by"] == "no_later_than"


# Settings, the CLI and the phone -----------------------------------------------------------


def test_settings_edit_and_clear_the_stop(board):
    home, _, _ = board
    Clock(home)
    model = Model(home)
    set_options(home, enabled=False)
    with pytest.raises(KanbanRefused, match="Self-work is off"):
        home.self_work.change_stop({"condition": "REM-1 is done"})
    set_options(home, enabled=True)
    view = home.self_work.change_stop(
        {"condition": "  REM-1   is done ", "no_later_than": {"time": "23:00"}}
    )
    stop = view["stop"]
    assert stop["condition"] == "REM-1 is done" and stop["no_later_than"]
    step(home)
    assert stop_view(home)["judgment"] is not None
    # Editing the condition starts it over with no judgment.
    view = home.self_work.change_stop(
        {"condition": "REM-2 is done", "expected_revision": stop_view(home)["revision"]}
    )
    assert view["stop"]["condition"] == "REM-2 is done" and view["stop"]["judgment"] is None
    with pytest.raises(KanbanRefused, match="changed"):
        home.self_work.change_stop({"condition": "REM-3 is done", "expected_revision": 0})
    with pytest.raises(KanbanRefused, match="isn't a time"):
        home.self_work.change_stop({"no_later_than": {"time": "25:00"}})
    with pytest.raises(KanbanRefused, match="already passed"):
        home.self_work.change_stop({"no_later_than": {"at": "2020-01-01T00:00:00+00:00"}})
    # Clearing one keeps the other; clearing both leaves no stop.
    view = home.self_work.change_stop({"clear": "condition"})
    assert view["stop"]["condition"] == "" and view["stop"]["no_later_than"]
    view = home.self_work.change_stop({"no_later_than": None})
    assert view["stop"]["active"] is False
    assert model.checks  # the check ran on the first condition


def test_turning_self_work_off_keeps_the_stop_as_the_last_one(board):
    home, _, _ = board
    clock = Clock(home)
    model = Model(home)
    start_session(home, clock, model, "REM-3 is done")
    step(home)
    model.say("off", "turn off self-work", {"action": "disable"})
    shown = stop_view(home)
    assert not shown["active"] and shown["last"]["condition"] == "REM-3 is done"
    assert shown["last"]["ended_by"] == "user" and shown["last"]["judgment"]["met"] is False
    # The next session starts without it.
    model.say("on", "turn on self-work", {"action": "enable"})
    assert not stop_view(home)["active"]
    status = model.say("status", "is self-work on?", {"action": "status"})
    assert "Stops when" not in status


def test_clear_history_keeps_the_users_stop_but_not_its_judgments(board):
    home, _, _ = board
    clock = Clock(home)
    model = Model(home)
    start_session(home, clock, model, "REM-4 is done", no_later_than={"time": "23:00"})
    step(home)
    assert stop_view(home)["judgment"] is not None
    conversation = home.store.conversation()["conversation"]["id"]
    home.clear_history({"request_id": "clear-1", "conversation_id": conversation, "confirm": True})
    kept = record(home)
    assert kept["condition"] == "REM-4 is done" and kept["no_later_than"]
    assert "judgment" not in kept and "judged_runs" not in kept or not kept["judged_runs"]


def test_only_the_user_changes_the_stop_over_http(api_server):
    request, service = api_server
    revision = service.self_work.view()["settings"]["revision"]
    service.self_work.update_settings({"enabled": True, "expected_revision": revision})
    changed = request(
        "POST",
        "/api/rem/kanban/stop",
        body={"condition": "REM-1 is done", "no_later_than": {"time": "23:00"}},
    )
    assert changed.status == 200, changed.json()
    assert changed.json()["self_work"]["stop"]["condition"] == "REM-1 is done"
    stale = request("POST", "/api/rem/kanban/stop", body={"clear": "both", "expected_revision": 0})
    assert stale.status == 409 and stale.json()["code"] == "conflict"
    bad = request("POST", "/api/rem/kanban/stop", body={"clear": "everything"})
    assert bad.status == 400
    assert agent_request_refusal("POST", "/api/rem/kanban/stop")


def test_the_cli_sets_shows_and_clears_the_stop(backend):
    cli("selfwork", "off")
    refused = cli("selfwork", "stop-when", "REM-1 is done", code=4)
    assert "Self-work is off" in refused["error"]["message"]
    cli("selfwork", "on")
    stop = cli("selfwork", "stop-when", "REM-1 is done", "--no-later-than", "23:59")["data"]["stop"]
    assert stop["condition"] == "REM-1 is done" and stop["no_later_than"]
    text = " ".join(runner.invoke(app, ["selfwork", "status", "--output", "text"]).stdout.split())
    assert "Stops when “REM-1 is done”, or at 11:59 PM" in text and "Not checked yet." in text
    assert cli("selfwork", "stop-when", "--clear", "everything", code=2)
    assert cli("selfwork", "stop-when", "--day", "today", code=2)
    stale = cli("selfwork", "stop-when", "--clear", "both", "--revision", "0", code=4)
    assert "changed" in stale["error"]["message"]
    cleared = cli("selfwork", "stop-when", "--clear", "both")["data"]["stop"]
    assert cleared["active"] is False
