"""Auto mode keeps Blocked for work only a person can unblock, and misfiled work moves itself.

With auto mode on, review requests, unresolved checks the sandbox can't prove and
"please confirm" questions don't park a ticket on the user. A ticket whose thread was
rooted in the wrong project moves to the assigned project its code is in and starts again
in a new thread, and tickets naming another project's files are filed there up front.
"""

import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gofer.ui import rem_auto_mode as auto_mode
from gofer.ui.organizations import AUTO_MODE_BRIEF, checkin_brief
from gofer.ui.rem_home import transient
from gofer.ui.rem_kanban import rem_assignment
from tests.unit.test_rem_kanban import add, at, set_options, settle, tick, ticket
from tests.unit.test_rem_kanban import board as board

# The text rules -----------------------------------------------------------------------------


def test_only_things_a_person_must_supply_count_as_human_only() -> None:
    for text in (
        "Which account should I deploy to?",
        "I need an API key for the weather service.",
        "Please sign in to GitHub first.",
        "The payment method was declined.",
    ):
        assert auto_mode.human_only(text), text
    for text in (
        "Could you run these tests in a normal terminal and tell me whether they pass?",
        "Can I push local dev to origin so CI runs?",
        "Please confirm you're ok with this change.",
    ):
        assert not auto_mode.human_only(text), text


def test_signoff_requests_are_recognised_and_stripped_from_rem_criteria() -> None:
    assert auto_mode.asks_signoff("Make sure the user's ok with it")
    assert auto_mode.asks_signoff("The user approves the new layout")
    assert auto_mode.asks_signoff("Check it on a real phone")
    assert not auto_mode.asks_signoff("All unit tests pass and the docs are updated")
    assert (
        auto_mode.strip_signoff("Tests pass. Make sure the user is ok with it. Docs updated.")
        == "Tests pass. Docs updated."
    )
    assert auto_mode.strip_signoff("Get the user's sign-off.") == ""


def test_only_unverifiable_unknowns_may_be_accepted() -> None:
    sandbox = {
        "result": "unknown",
        "evidence": "Unverifiable here: bwrap can't create namespaces in this sandbox.",
    }
    device = {"result": "unknown", "evidence": "Needs a check on a real phone."}
    missing = {"result": "unknown", "evidence": "No test covers the new flag."}
    failed = {"result": "fail", "evidence": "Unverifiable here: and the code is wrong"}
    passed = {"result": "pass", "evidence": "1 passed"}
    assert auto_mode.limitations([passed, sandbox, device]) == [
        sandbox["evidence"],
        device["evidence"],
    ]
    assert auto_mode.limitations([passed, missing]) is None
    assert auto_mode.limitations([failed]) is None
    note = auto_mode.limitation_note([sandbox["evidence"]])
    assert note.startswith("Not verified here (auto mode): bwrap")


def projects(tmp_path: Path) -> tuple[Path, Path]:
    vault = tmp_path / "vault"
    code = tmp_path / "code"
    (vault / "notes").mkdir(parents=True)
    (vault / "notes" / "idea.md").write_text("x")
    (code / "src" / "app").mkdir(parents=True)
    (code / "src" / "app" / "server.py").write_text("x = 1\n")
    return vault, code


def test_the_project_is_picked_from_file_paths(tmp_path: Path) -> None:
    vault, code = projects(tmp_path)
    roots = [str(vault), str(code)]
    text = "Fix the handler in src/app/server.py so a bad call can't stop it."
    assert auto_mode.pick_project(text, str(vault), roots) == str(code)
    assert auto_mode.file_under({"description": text, "project_dir": str(vault)}, roots) == str(
        code
    )
    # A ticket naming files in its own project stays put, and so does one naming none.
    own = {"description": "Tidy notes/idea.md and src/app/server.py", "project_dir": str(vault)}
    assert auto_mode.file_under(own, roots) is None
    assert (
        auto_mode.file_under({"description": "Make it faster", "project_dir": str(vault)}, roots)
        is None
    )
    # A worker complaining about its own root names that root; it doesn't count for it.
    complaint = (
        f"I'm blocked: this thread is rooted in {vault}, and {code} is read-only here "
        "(Read-only file system). Rerun it with that folder."
    )
    blocked = {"project_dir": str(vault), "description": "Fix the handler", "block_reason": ""}
    assert auto_mode.wrong_root(complaint)
    assert auto_mode.reroot_target(blocked, {"worker_result": complaint}, roots) == str(code)
    # Without a wrong-root complaint nothing moves, whatever paths the result mentions.
    fine = {"worker_result": f"Done; I also read {code}/src/app/server.py."}
    assert auto_mode.reroot_target(blocked, fine, roots) is None
    # A run already marked with its right project needs no text at all.
    assert auto_mode.reroot_target(blocked, {"wrong_project": str(code)}, roots) == str(code)


def test_only_a_refused_write_elsewhere_or_nothing_built_counts_as_misrooted(
    tmp_path: Path,
) -> None:
    vault, code = projects(tmp_path)
    roots = [str(vault), str(code)]
    report = f"Report: [r](<{vault}/reports/rem/Reports/Raticode/r.html>)"
    # Finished work that mentions a sandbox write limit and links a report elsewhere.
    landed = (
        "REM-170 is done and landed on local dev at 1234abcd. The fix is in src/app/server.py. "
        "The sandbox cannot write to ~/.cache, so I used my scratch folder. " + report
    )
    assert auto_mode.misrooted_target(landed, str(code), roots) is None
    unlanded = landed.replace("is done and landed on local dev at 1234abcd", "is done")
    assert auto_mode.misrooted_target(unlanded, str(code), roots) is None
    # A report saved under another project is an output, not where the code is.
    saved = f"Done. I saved {vault}/reports/x.html because the vault is read-only for edits."
    assert auto_mode.misrooted_target(saved, str(code), roots) is None
    # "Nothing landed" isn't a landing.
    assert not auto_mode.landed("Nothing was built, committed or landed on dev.")
    assert auto_mode.landed("I landed the fix on local `dev` as `548fa21d`.")
    # A refused write in another assigned project, with nothing built.
    refused = (
        f"Nothing was built. touch: cannot touch '{code}/.probe': Read-only file system. "
        "Nothing landed on dev."
    )
    assert auto_mode.misrooted_target(refused, str(vault), roots) == str(code)
    # Nothing built for a wrong-root reason, naming the folder the rerun needs.
    named = (
        f"I changed nothing: this thread is rooted in `{vault}`, and the other repo is "
        f"read-only here. The rerun needs a new thread rooted in `{code}`."
    )
    assert auto_mode.misrooted_target(named, str(vault), roots) == str(code)
    # A project path that ends a sentence still counts.
    assert auto_mode.RELATIVE_PATH.findall("The fix is in src/app/server.py.") == [
        "src/app/server.py"
    ]
    assert auto_mode.pick_project("Change src/app/server.py.", str(vault), roots) == str(code)


def test_a_locked_database_is_a_temporary_failure() -> None:
    assert transient(sqlite3.OperationalError("database is locked"))
    assert not transient(ValueError("can't be completed without sign-in"))


def test_organization_workers_are_told_not_to_wait_for_sign_off_in_auto_mode() -> None:
    plain = checkin_brief(["/p/x"], ["X-1 (failed)"])
    assert AUTO_MODE_BRIEF not in plain and "If it needs the user or" in plain
    auto = checkin_brief(["/p/x"], ["X-1 (failed)"], auto=True)
    assert AUTO_MODE_BRIEF in auto
    assert "credentials, an account, a payment" in auto


# On the board -------------------------------------------------------------------------------


def assign(home: Any, *roots: Path) -> None:
    with home.store.read() as db:
        config = home.store.setting(db, "config")
        revision = rem_assignment(db, home.store)["revision"]
    home.configure({**config, "projects": [{"root": str(r), "name": r.name} for r in roots]})
    home.kanban.set_participant_projects(
        "user",
        {
            "participant_id": "rem",
            "projects": [str(r) for r in roots],
            "expected_revision": revision,
        },
    )


def runs_of(home: Any, title: str) -> list[dict[str, Any]]:
    with home.store.read() as db:
        rows = [json.loads(r[0]) for r in db.execute("SELECT body FROM runs")]
    return sorted((r for r in rows if r["recipe"]["title"] == title), key=lambda r: r["created_at"])


def timeline_titles(home: Any, title: str) -> list[str]:
    found = ticket(home, title)
    entries = home.kanban.timeline({"ticket_id": found["ticket_id"], "scope": "rem"})["comments"]
    return [e["title"] for e in entries]


def wait_until(home: Any, check: Any, seconds: float = 8) -> None:
    deadline = time.monotonic() + seconds
    while not check() and time.monotonic() < deadline:
        tick(home)
        time.sleep(0.01)
    assert check()


def test_a_ticket_run_in_the_wrong_project_moves_and_restarts_in_a_new_thread(board, tmp_path):
    home, fake, _ = board
    vault, code = projects(tmp_path)
    assign(home, vault, code)
    fake.answers["Handler"] = (
        f"I'm blocked: this thread is rooted in {vault}, and {code} is read-only here "
        f"(touch: Read-only file system). The fix goes in {code}/src/app/server.py."
    )
    add(home, "Handler", project_dir=str(vault), description="Keep the tool server up.")
    at(home, 15)
    tick(home)
    wait_until(home, lambda: len(runs_of(home, "Handler")) >= 2)
    first, second = runs_of(home, "Handler")[:2]
    # The first run ended without a review or a repair sent back to its thread.
    assert first["state"] == "blocked" and first["wrong_project"] == str(code)
    assert first["repair_count"] == 0 and "wrong project" in first["summary_short"]
    reviews = [
        c
        for c in fake.calls
        if "independent read-only reviewer" in c["agent_instructions"]
        and first["thread_id"] in c["conversation_id"]
    ]
    assert not reviews
    # The ticket moved to the project its code is in, and its new run is rooted there.
    moved = ticket(home, "Handler")
    assert moved["project_dir"] == str(code)
    assert second["recipe"]["project_dir"] == str(code)
    assert second["thread_id"] != first["thread_id"]
    assert "Moved to the project its code is in" in timeline_titles(home, "Handler")
    settle(home, "Handler")
    assert ticket(home, "Handler")["status"] == "done"
    assert len(runs_of(home, "Handler")) == 2


def test_a_finished_ticket_that_links_a_report_elsewhere_still_goes_to_review(board, tmp_path):
    home, fake, _ = board
    vault, code = projects(tmp_path)
    assign(home, vault, code)
    report = f"Report: [r](<{vault}/reports/rem/Reports/Raticode/r.html>)"
    fake.answers["Landed"] = (
        "Done and landed on local dev at 1234abcd. The fix is in src/app/server.py. "
        "The sandbox cannot write to ~/.cache, so I used my scratch folder. " + report
    )
    fake.answers["Unlanded"] = (
        "Done. The fix is in src/app/server.py. The sandbox cannot write to ~/.cache, so I "
        "used my scratch folder. " + report
    )
    for title in ("Landed", "Unlanded"):
        add(home, title, project_dir=str(code), description="Keep the tool server up.")
    at(home, 15)
    tick(home)
    settle(home, "Landed", "Unlanded")
    for title in ("Landed", "Unlanded"):
        assert ticket(home, title)["status"] == "done", title
        assert ticket(home, title)["project_dir"] == str(code)
        (run,) = runs_of(home, title)
        assert run["state"] == "succeeded" and not run.get("wrong_project")
        reviews = [
            c
            for c in fake.calls
            if "independent read-only reviewer" in c["agent_instructions"]
            and run["thread_id"] in c["conversation_id"]
        ]
        assert reviews, title
        assert "Moved to the project its code is in" not in timeline_titles(home, title)


def test_a_ticket_naming_another_projects_files_is_filed_there_before_it_starts(board, tmp_path):
    home, _, _ = board
    vault, code = projects(tmp_path)
    assign(home, vault, code)
    add(
        home,
        "Snapshot",
        project_dir=str(vault),
        description="Replace the bare next() in src/app/server.py with a default.",
    )
    at(home, 15)
    tick(home)
    settle(home, "Snapshot")
    assert ticket(home, "Snapshot")["project_dir"] == str(code)
    assert [r["recipe"]["project_dir"] for r in runs_of(home, "Snapshot")] == [str(code)]


def blocked_on_question(home: Any, title: str, question: str) -> Any:
    from tests.unit.test_ticket_timeline import begin, put_run

    current = begin(home, title)
    put_run(
        home,
        {
            **current,
            "state": "blocked",
            "needs_user": True,
            "user_question": question,
            "summary_short": question,
            "finished_at": datetime.now(UTC).isoformat(),
        },
    )
    home.self_work.reconcile()
    assert ticket(home, title)["status"] == "blocked"
    return current


def test_auto_mode_doesnt_wait_for_the_users_confirmation(board):
    home, _, _ = board
    set_options(home, auto_approve_proposals=True, confirm_auto_approve=True)
    blocked_on_question(home, "Confirm", "Could you confirm you're ok with this before I land it?")
    blocked_on_question(home, "Keys", "I need an API key for the weather service.")
    at(home, 15)
    wait_until(home, lambda: len(runs_of(home, "Confirm")) >= 2)
    settle(home, "Confirm")
    assert ticket(home, "Confirm")["status"] == "done"
    # A missing credential still needs the user, auto mode or not.
    keys = ticket(home, "Keys")
    assert keys["status"] == "blocked" and len(runs_of(home, "Keys")) == 1
    assert "Still blocked" in timeline_titles(home, "Keys")


def test_without_auto_mode_a_question_still_waits_for_the_user(board):
    home, _, _ = board
    blocked_on_question(home, "Confirm", "Could you confirm you're ok with this before I land it?")
    at(home, 15)
    for _ in range(5):
        tick(home)
    assert ticket(home, "Confirm")["status"] == "blocked"
    assert len(runs_of(home, "Confirm")) == 1


def reviewer_says(fake: Any, evidence: str) -> Any:
    """A reviewer that leaves the first criterion unknown with this evidence."""

    async def source(**kwargs: Any) -> Any:
        if "independent read-only reviewer" not in kwargs["agent_instructions"]:
            async for event in fake(**kwargs):
                yield event
            return
        fake.calls.append(kwargs)
        with fake.home.store.read() as db:
            runs = [json.loads(r[0]) for r in db.execute("SELECT body FROM runs")]
        run = next(
            r
            for r in runs
            if (r.get("verification_thread_id") or "\0") in kwargs["conversation_id"]
        )
        criteria = run["recipe"]["acceptance_criteria"]
        yield {
            "type": "thought",
            "text": "Independent check",
            "trace": {
                "id": "review-check",
                "category": "shell",
                "phase": "result",
                "status": "completed",
                "exitCode": 0,
                "output": "1 passed",
            },
        }
        yield {
            "type": "final",
            "message": {
                "body": json.dumps(
                    {
                        "criteria": [
                            {
                                "id": c["id"],
                                "result": "unknown" if i == 0 else "pass",
                                "evidence": evidence if i == 0 else "1 passed",
                            }
                            for i, c in enumerate(criteria)
                        ],
                        "feedback": "Run the bubblewrap tests outside the sandbox.",
                    }
                )
            },
        }

    return source


def test_auto_mode_records_what_the_sandbox_cant_prove_instead_of_blocking(board):
    home, fake, _ = board
    set_options(home, auto_approve_proposals=True, confirm_auto_approve=True)
    home.source = reviewer_says(
        fake, "Unverifiable here: bwrap can't create namespaces inside this sandbox."
    )
    add(home, "Overlay")
    at(home, 15)
    tick(home)
    settle(home, "Overlay")
    assert ticket(home, "Overlay")["status"] == "done"
    run = runs_of(home, "Overlay")[-1]
    assert run["state"] == "succeeded" and run["repair_count"] == 0
    assert run["unverified"].startswith("Not verified here (auto mode): bwrap")
    found = ticket(home, "Overlay")
    entries = home.kanban.timeline({"ticket_id": found["ticket_id"], "scope": "rem"})["comments"]
    assert "Not verified here" in entries[-1]["body"]
    # This ticket's own review: research self-work started beside it has reviews of its own.
    review_thread = run["verification_thread_id"]
    reviews = [
        c
        for c in fake.calls
        if "independent read-only reviewer" in c["agent_instructions"]
        and review_thread in c["conversation_id"]
    ]
    assert "Auto mode is on" in reviews[-1]["agent_instructions"]
    workers = [
        c
        for c in fake.calls
        if c["workflow"]["chatThreadTitle"] == "Rem task: Overlay"
        and "independent read-only reviewer" not in c["agent_instructions"]
    ]
    assert auto_mode.UNVERIFIABLE in workers[-1]["agent_instructions"]


def test_without_auto_mode_an_unproven_check_still_blocks(board):
    home, fake, _ = board
    home.source = reviewer_says(
        fake, "Unverifiable here: bwrap can't create namespaces inside this sandbox."
    )
    add(home, "Overlay")
    at(home, 15)
    tick(home)
    settle(home, "Overlay")
    assert ticket(home, "Overlay")["status"] == "blocked"
    calls = [c for c in fake.calls if "Auto mode is on" in c["agent_instructions"]]
    assert not calls


def test_auto_mode_drops_sign_off_from_rems_own_finish_line(board):
    home, _, _ = board
    created = add(home, "Layout", done_when="Tests pass. Make sure the user is ok with it.")
    with home.store.connect() as db:
        db.execute(
            "UPDATE kanban_tickets SET body=json_set(body, '$.created_by', 'self_work') WHERE id=?",
            (created["id"],),
        )
    from gofer.ui.rem_kanban import get_ticket

    with home.store.read() as db:
        stored = get_ticket(db, created["id"])
    before = home.self_work._recipe(stored).acceptance_criteria
    assert before[0].description.endswith("ok with it.")
    assert before[0].authored_by == "rem_recommended"
    set_options(home, auto_approve_proposals=True, confirm_auto_approve=True)
    after = home.self_work._recipe(stored).acceptance_criteria
    assert [c.description for c in after] == ["Tests pass."]
    # The user's own finish line is theirs to keep.
    mine = add(home, "Mine", done_when="Make sure the user is ok with it.")
    with home.store.read() as db:
        theirs = home.self_work._recipe(get_ticket(db, mine["id"])).acceptance_criteria
    assert theirs[0].authored_by == "user" and "ok with it" in theirs[0].description
