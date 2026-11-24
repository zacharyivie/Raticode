"""Raticode runs a project's decided check command itself before a writer task passes.

A confirmed command always runs; Rem's provisional one runs only while self-work's auto
mode is on. A failure goes back to the worker with its output and no reviewer turn. The
command runs in an OS sandbox (no network, credentials hidden, writes only to its
folder and a scratch folder), and never without it.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import rem_decisions as decisions
from gofer.ui import rem_project_checks as checks
from gofer.ui.rem_home import RemHome
from tests.unit.test_rem_home import home, recipe, wait_run  # noqa: F401
from tests.unit.test_rem_home_landing import passing_review, shell
from tests.unit.test_rem_kanban import board as board  # noqa: F401

RESOURCES = {"shell": True, "web": False, "agents": False, "browser": False, "skills": []}
CHECK = "bash check.sh"


@pytest.fixture(autouse=True)
def plain_sandbox(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """How Rem drives a check, tested on any computer: the sandbox is a pass-through.

    Tests that use the ``real_sandbox`` fixture get the real one.
    """
    if "real_sandbox" in request.fixturenames:
        return
    monkeypatch.setattr(checks, "sandbox_problem", lambda *a, **k: None)
    monkeypatch.setattr(
        checks, "sandboxed_command", lambda command, folder, **k: ["/bin/sh", "-c", command]
    )


def project(tmp_path: Path) -> Path:
    """A plain folder whose check passes only once fixed.txt exists, and logs each run."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "check.sh").write_text(
        'echo run >> ran.log\nif [ -f fixed.txt ]; then echo "all good"; exit 0; fi\n'
        'echo "FAIL: fixed.txt is missing"; exit 1\n'
    )
    return root


def runs_of(root: Path) -> int:
    log = root / "ran.log"
    return len(log.read_text().splitlines()) if log.exists() else 0


def source_for(root: Path, *, fix_on_repair: bool):
    calls: dict[str, list[dict[str, Any]]] = {"work": [], "review": []}

    async def source(**kwargs: Any):
        if "independent read-only reviewer" in kwargs["agent_instructions"]:
            calls["review"].append(kwargs)
            for event in passing_review():
                yield event
            return
        calls["work"].append(kwargs)
        if len(calls["work"]) > 1 and fix_on_repair:
            (root / "fixed.txt").write_text("ok\n")
        # The worker's only command proves nothing on its own.
        yield shell("ls")
        yield {"type": "final", "message": {"body": f"Turn {len(calls['work'])} done."}}

    return source, calls


def decide(home: RemHome, root: Path, command: str = CHECK) -> dict[str, Any]:  # noqa: F811
    view = home.change_decisions(
        {"action": "add", "topic": "checks.fast", "answer": command, "project": str(root)}
    )
    return next(i for i in view["items"] if i["topic"] == "checks.fast")


def test_a_failing_check_goes_back_to_the_worker_before_any_review(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = project(tmp_path)
    home.configure({"provider": "claude_code", "resources": RESOURCES})
    item = decide(home, root)
    source, calls = source_for(root, fix_on_repair=True)
    home.source = source
    task = home.create_task({"request_id": "check", **recipe(root)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=20)
    assert run["state"] == "succeeded", run
    # Turn 1 failed Raticode's check twice (the rerun confirmed it): no reviewer saw it.
    # Turn 2 fixed it, then one review.
    assert len(calls["work"]) == 2 and len(calls["review"]) == 1
    assert runs_of(root) == 3
    first = calls["work"][0]["agent_instructions"]
    assert f"Raticode runs this project's decided check ({item['id']}) itself" in first
    repair = calls["work"][1]["messages"][-1]["body"]
    assert f"Raticode ran this project's check ({item['id']}) itself" in repair
    assert "FAIL: fixed.txt is missing" in repair
    with home.store.read() as db:
        stored = home.store.get(db, "runs", run["id"])
    assert stored["project_check"]["passed"] and stored["project_check"]["command"] == CHECK
    assert not stored["project_check"].get("flaky")
    # The reviewer is told Raticode's own result.
    review = calls["review"][0]["agent_instructions"]
    assert "Raticode ran `bash check.sh`" in review and "passed" in review
    decided = next(i for i in home.decisions(str(root))["items"] if i["id"] == item["id"])
    assert decided["last_check"]["passed"] and decided["failures"] == 0
    assert decided["status"] == "confirmed" and decided["answer"] == CHECK


def test_the_check_after_the_review_can_be_stopped_with_its_run(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = project(tmp_path)
    (root / "fixed.txt").write_text("ok\n")
    home.configure({"provider": "claude_code", "resources": RESOURCES})
    decide(home, root)
    source, _ = source_for(root, fix_on_repair=False)
    home.source = source
    seen: list[tuple[bool, Any]] = []
    original = home._project_check

    def spy(run: dict[str, Any], traces: Any, cancel: Any, **kwargs: Any) -> Any:
        seen.append((bool(kwargs.get("after_review")), cancel))
        return original(run, traces, cancel, **kwargs)

    home._project_check = spy  # type: ignore[method-assign]
    task = home.create_task({"request_id": "check-stop", **recipe(root)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=20)
    assert run["state"] == "succeeded", run
    after = [cancel for late, cancel in seen if late]
    assert after and all(isinstance(c, threading.Event) for c in after), seen


def test_a_check_that_keeps_failing_blocks_the_task_with_its_result(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = project(tmp_path)
    home.configure({"provider": "claude_code", "resources": RESOURCES})
    decide(home, root)
    source, calls = source_for(root, fix_on_repair=False)
    home.source = source
    task = home.create_task({"request_id": "stuck", **recipe(root)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=20)
    assert run["state"] == "blocked", run
    assert calls["review"] == []
    assert "The project's check still fails after two repairs" in run["summary_short"]
    # The work broke the check, not the command: the decision isn't asked again.
    item = next(i for i in home.decisions(str(root))["items"] if i["topic"] == "checks.fast")
    assert item["failures"] == 0 and item["status"] == "confirmed"


def provisional(home: RemHome, root: Path) -> dict[str, Any]:  # noqa: F811
    request = decisions.DecisionRequest.model_validate(
        {
            "topic": "checks.fast",
            "question": "Which command shows that work in proj is sound before it lands?",
            "recommendation": {
                "answer": CHECK,
                "evidence": [{"kind": "file", "ref": "check.sh"}],
            },
        }
    )
    # Only a project scan can file the check command provisionally.
    with home.store.connect() as db:
        outcome, item = decisions.file_request(
            db, home.store, request, project=str(root), decides_alone=True, source={"via": "scan"}
        )
    assert outcome == "provisional"
    return item


def test_a_provisional_command_never_runs_unattended_with_auto_mode_off(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = project(tmp_path)
    home.configure({"provider": "claude_code", "resources": RESOURCES})
    provisional(home, root)
    source, calls = source_for(root, fix_on_repair=False)
    home.source = source
    task = home.create_task({"request_id": "off", **recipe(root)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=20)
    # Today's gate applies instead: the worker's own receipts and the review.
    assert run["state"] == "succeeded", run
    assert runs_of(root) == 0
    assert "decided check" not in calls["work"][0]["agent_instructions"]


def test_with_auto_mode_on_rem_runs_its_provisional_check(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = project(tmp_path)
    (root / "fixed.txt").write_text("ok\n")
    with home.store.connect() as db:
        home.store.set_setting(db, "self_work", {"enabled": True, "auto_approve_proposals": True})
    home.configure({"provider": "claude_code", "resources": RESOURCES})
    item = provisional(home, root)
    source, calls = source_for(root, fix_on_repair=False)
    home.source = source
    task = home.create_task({"request_id": "on", **recipe(root)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=20)
    assert run["state"] == "succeeded", run
    assert runs_of(root) == 1
    with home.store.read() as db:
        stored = home.store.get(db, "runs", run["id"])
    assert stored["project_check"]["decision_status"] == "provisional"
    assert stored["project_check"]["decision_id"] == item["id"]


def test_only_a_command_that_cannot_run_counts_toward_asking_again(tmp_path: Path):
    root = tmp_path / "p"
    root.mkdir()
    data = tmp_path / "data"
    missing = checks.run("definitely-not-a-command-xyz --check", root, data_dir=data)
    assert not missing["passed"] and checks.broken(missing)
    failing = checks.run("exit 1", root, data_dir=data)
    assert not failing["passed"] and not checks.broken(failing)
    passing = checks.run("echo fine", root, data_dir=data)
    assert passing["passed"] and "fine" in passing["output"]
    assert "Raticode ran `echo fine` in its sandbox" in checks.summary(passing)
    # Pushes are blocked, temporary files go to a scratch folder, removed afterwards.
    env = checks.run("env", root, data_dir=data)["output"]
    assert ".pushInsteadOf" in env
    temp = checks.run("printenv TMPDIR", root, data_dir=data)["output"]
    assert temp.startswith(f"{data / checks.SCRATCH}/")
    assert list((data / checks.SCRATCH).iterdir()) == []


def test_without_its_sandbox_a_check_never_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "p"
    root.mkdir()
    monkeypatch.setattr(
        checks, "sandbox_problem", lambda *a, **k: "bubblewrap (bwrap) isn't installed"
    )
    result = checks.run("touch ran", root, data_dir=tmp_path / "data")
    assert result["skipped"] == "bubblewrap (bwrap) isn't installed"
    assert not result["passed"] and not (root / "ran").exists()


def test_the_sandbox_reasons_per_operating_system():
    assert "operating system" in (checks.real_problem("win32") or "")
    assert checks.real_problem("linux", which=lambda name: None) == (
        "bubblewrap (bwrap) isn't installed"
    )
    found = checks.real_problem("linux", which=lambda name: "/usr/bin/bwrap", probe=lambda: None)
    assert found is None


def test_with_no_sandbox_rem_falls_back_to_the_review(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = project(tmp_path)
    monkeypatch.setattr(checks, "sandbox_problem", lambda *a, **k: "no sandbox here")
    home.configure({"provider": "claude_code", "resources": RESOURCES})
    decide(home, root)
    source, calls = source_for(root, fix_on_repair=False)
    home.source = source
    task = home.create_task({"request_id": "nosandbox", **recipe(root)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=20)
    # Nothing ran outside a sandbox; the worker's receipts and the review decided.
    assert runs_of(root) == 0
    assert run["state"] == "succeeded", run
    assert len(calls["review"]) == 1
    assert "decided check" not in calls["work"][0]["agent_instructions"]


@pytest.fixture
def real_sandbox() -> None:
    """The real check sandbox; skipped where it can't start (no bubblewrap, no userns)."""
    problem = checks.sandbox_problem()
    if problem:
        pytest.skip(f"Needs the check sandbox: {problem}")


@pytest.fixture
def listener() -> Iterator[int]:
    """A TCP port on this computer that accepts connections outside the sandbox."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    stop = threading.Event()

    def serve() -> None:
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                connection, _ = server.accept()
                connection.close()
            except OSError:
                continue

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield server.getsockname()[1]
    stop.set()
    thread.join(2)
    server.close()


def test_the_check_cant_read_credentials_or_reach_the_network(
    real_sandbox: None, tmp_path: Path, listener: int
):
    ssh = Path(os.path.expanduser("~")) / ".ssh"
    ssh.mkdir(exist_ok=True)
    (ssh / "id_check_probe").write_text("SECRET-KEY\n")
    data = tmp_path / "data"
    (data / "devices").mkdir(parents=True)
    (data / "devices" / "key").write_text("DEVICE-KEY\n")
    root = tmp_path / "work"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        connect = (
            f'{sys.executable} -c "import socket; '
            f"socket.create_connection(('127.0.0.1', {listener}), timeout=3)\""
        )
        # Outside the sandbox the same connection works, so the port is really open.
        socket.create_connection(("127.0.0.1", listener), timeout=3).close()
        script = "\n".join(
            [
                f"cat {ssh / 'id_check_probe'} && echo READ-SSH",
                f"cat {data / 'devices' / 'key'} && echo READ-DATA",
                f"{connect} && echo NETWORK",
                f"echo x > {outside / 'leak'} && echo WROTE-OUTSIDE",
                'echo inside > in-folder.txt && echo x > "$TMPDIR/t" && echo WROTE-OK',
            ]
        )
        result = checks.run(script, root, data_dir=data)
    finally:
        (ssh / "id_check_probe").unlink()
    assert result["sandboxed"] is True, result
    output = result["output"]
    assert "SECRET-KEY" not in output and "READ-SSH" not in output
    assert "DEVICE-KEY" not in output and "READ-DATA" not in output
    assert "NETWORK" not in output
    assert "WROTE-OUTSIDE" not in output and not (outside / "leak").exists()
    # It still works where it should: its own folder and its scratch.
    assert "WROTE-OK" in output and (root / "in-folder.txt").read_text() == "inside\n"


def test_a_decided_command_that_cannot_run_twice_is_asked_again(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = project(tmp_path)
    (root / "fixed.txt").write_text("ok\n")
    home.configure({"provider": "claude_code", "resources": RESOURCES})
    item = decide(home, root, "no-such-check-tool-xyz")
    for attempt in range(2):
        source, _ = source_for(root, fix_on_repair=False)
        home.source = source
        task = home.create_task({"request_id": f"broken-{attempt}", **recipe(root)})["task"]
        wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=20)
    current = next(i for i in home.decisions(str(root))["items"] if i["id"] == item["id"])
    assert current["failures"] >= 2
    assert "failed" in decisions.stale_reason(current)
    assert json.dumps(current["last_check"]).count("127")


def git(cwd: Path, *args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def committed_project(tmp_path: Path) -> tuple[Path, str]:
    """A git project whose check passes, and its commit."""
    import subprocess

    root = project(tmp_path)
    (root / "fixed.txt").write_text("ok\n")
    (root / ".gitignore").write_text("ran.log\n")
    # The check runs in a fresh worktree of the commit, so it logs its runs here.
    script = (root / "check.sh").read_text()
    (root / "check.sh").write_text(script.replace(">> ran.log", f">> {root / 'ran.log'}"))
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(root)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "start"], check=True)
    commit = subprocess.run(
        [*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return root, commit


def review_payload(prompt: str, key: str) -> Any:
    """The value of ``key`` in the JSON that ends a review prompt."""
    found = re.search(rf'"{key}":\s*', prompt)
    assert found, key
    start = found.end()
    return json.JSONDecoder().raw_decode(prompt, start)[0]


def test_the_reviewer_gets_raticodes_passing_check_as_a_trusted_receipt(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root, commit = committed_project(tmp_path)
    home.configure({"provider": "claude_code", "resources": RESOURCES})
    item = decide(home, root)
    source, calls = source_for(root, fix_on_repair=False)
    home.source = source
    task = home.create_task({"request_id": "receipt", **recipe(root)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=20)
    assert run["state"] == "succeeded", run
    review = calls["review"][0]["agent_instructions"]
    receipt = review_payload(review, "raticode_check")
    assert receipt["command"] == CHECK
    assert receipt["exit_code"] == 0 and receipt["passed"] is True
    assert receipt["commit"] == commit
    assert receipt["decision_id"] == item["id"]
    assert isinstance(receipt["seconds"], int | float)
    assert "all good" in receipt["output_tail"]
    assert receipt["produced_by"] == "Raticode, not the worker"
    # Labelled as more trustworthy than the worker's own receipts.
    assert checks.REVIEW_GUIDANCE in review
    assert "Trust it over the worker's receipts" in review
    # It is its own field, not mixed into the worker's receipts.
    assert all(r.get("category") != "raticode_check" for r in review_payload(review, "receipts"))


def test_without_a_check_run_the_reviewer_gets_no_raticode_receipt(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root, _ = committed_project(tmp_path)
    home.configure({"provider": "claude_code", "resources": RESOURCES})
    source, calls = source_for(root, fix_on_repair=False)
    home.source = source
    task = home.create_task({"request_id": "nocheck", **recipe(root)})["task"]
    wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=20)
    review = calls["review"][0]["agent_instructions"]
    assert '"raticode_check"' not in review and checks.REVIEW_GUIDANCE not in review


def test_develop_work_names_its_proving_test_and_the_reviewer_looks_for_it(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    from gofer.ui.rem_home import PROVING_TEST_GUIDANCE, REVIEW_PROVING_TEST

    root, _ = committed_project(tmp_path)
    home.configure({"provider": "claude_code", "resources": RESOURCES})
    decide(home, root)
    source, calls = source_for(root, fix_on_repair=False)
    home.source = source
    task = home.create_task({"request_id": "proving", **recipe(root)})["task"]
    wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=20)
    work = calls["work"][0]["agent_instructions"]
    assert PROVING_TEST_GUIDANCE in work
    assert "Proving test: <test name>" in work
    assert "fails before the change and passes after it" in work
    assert "existing test that already covers it" in work
    assert "Not testable here because <reason>" in work
    review = calls["review"][0]["agent_instructions"]
    assert REVIEW_PROVING_TEST in review
    assert "raticode_check's output_tail or in a worker receipt" in review
    assert "Judge a not-testable reason" in review
    # The reviewer's own instructions don't ask it to name a test.
    assert PROVING_TEST_GUIDANCE not in review


def test_only_tasks_that_write_code_are_asked_for_a_proving_test():
    from gofer.ui.rem_home import proving_test_asked

    assert proving_test_asked({"type": "develop"})
    assert proving_test_asked({"type": "maintain"})
    assert proving_test_asked({"type": "maintain", "execution_mode": "task"})
    # Research and conversations run read-only; errands and reminders don't change code.
    assert not proving_test_asked({"type": "research"})
    assert not proving_test_asked({"type": "maintain", "execution_mode": "conversation"})
    assert not proving_test_asked({"type": "develop", "execution_mode": "conversation"})
    assert not proving_test_asked({"type": "errand"})
    assert not proving_test_asked({"type": "reminder"})


def test_maintain_work_names_its_proving_test_and_the_reviewer_looks_for_it(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    from gofer.ui.rem_home import PROVING_TEST_GUIDANCE, REVIEW_PROVING_TEST

    root, _ = committed_project(tmp_path)
    home.configure({"provider": "claude_code", "resources": RESOURCES})
    source, calls = source_for(root, fix_on_repair=False)
    home.source = source
    task = home.create_task(
        {"request_id": "maintain", **recipe(root, type="maintain", title="Fix totals")}
    )["task"]
    wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=20)
    work = calls["work"][0]["agent_instructions"]
    assert PROVING_TEST_GUIDANCE in work
    assert "Proving test: <test name>" in work
    assert "Not testable here because <reason>" in work
    review = calls["review"][0]["agent_instructions"]
    assert REVIEW_PROVING_TEST in review
    # A Guardrail ticket asks for the before and after receipts; the reviewer checks both.
    assert "look for both receipts" in review
    assert PROVING_TEST_GUIDANCE not in review


# A flaky check: rerun once on the same commit before any repair --------------------------------

FLAKY_TEST = "tests/test_clock.py::test_racy"


def flaky_project(root: Path) -> Path:
    """``root`` with a check whose first run fails a test and every later run passes."""
    root.mkdir(exist_ok=True)
    (root / "check.sh").write_text(
        "echo run >> ran.log\n"
        'if [ -f flaked.txt ]; then echo "= 3 passed ="; exit 0; fi\n'
        "touch flaked.txt\n"
        f'echo "FAILED {FLAKY_TEST}[slow] - AssertionError: took 2.1s"\n'
        'echo "= 1 failed, 2 passed ="; exit 1\n'
    )
    return root


def test_a_check_that_fails_once_then_passes_goes_to_review_marked_flaky(board):  # noqa: F811
    from gofer.core.usage_history import resolve_range
    from gofer.ui import rem_verified_work as verified
    from tests.unit.test_rem_kanban import add, at, settle, tick
    from tests.unit.test_ticket_timeline import latest_run, timeline

    rem, fake, _ = board
    root = flaky_project(rem.data_dir.parent / "project")
    item = decide(rem, root)
    add(rem, "Flaky")
    at(rem, 15)
    tick(rem)
    settle(rem, "Flaky")
    run = latest_run(rem, "Flaky")
    assert run["state"] == "succeeded", run
    # One worker turn, no repair: the rerun on the same commit passed and the review saw it.
    assert run["repair_count"] == 0 and runs_of(root) == 2
    reviews = [
        c["agent_instructions"]
        for c in fake.calls
        if "independent read-only reviewer" in c["agent_instructions"]
        and run["verification_thread_id"] in c["conversation_id"]
    ]
    receipt = review_payload(reviews[0], "raticode_check")
    assert receipt["passed"] is True and receipt["flaky"] is True
    assert "3 passed" in receipt["output_tail"]
    # The reviewer also sees the failed first run, not only the pass.
    assert f"FAILED {FLAKY_TEST}" in receipt["first_run_output_tail"]
    check = run["project_check"]
    assert check["passed"] and check["flaky"] and not check.get("confirmed")
    assert check["first_exit_code"] == 1 and f"FAILED {FLAKY_TEST}" in check["first_output"]
    assert run["project_check_flakes"] == 1
    # No check send-back in the ticket's timeline.
    entries = timeline(rem, "Flaky")
    assert not any(str(e.get("details") or "").startswith(checks.FEEDBACK_START) for e in entries)
    assert entries[-1]["title"] == "Verified complete, moved to Done"
    # The failing test is saved as the project's flake record.
    with rem.store.read() as db:
        flakes = rem.store.setting(db, checks.FLAKES_SETTING)["projects"][str(root)]
    assert [(f["run_id"], f["tests"]) for f in flakes] == [(run["id"], [FLAKY_TEST])]
    # The decision counts a pass; its record leaves the output out.
    decided = next(i for i in rem.decisions(str(root))["items"] if i["id"] == item["id"])
    assert decided["failures"] == 0 and decided["last_check"]["flaky"] is True
    assert "first_output" not in decided["last_check"]
    # Usage › Verified work counts it on its own, not as a first-try failure.
    numbers = verified.verified_work(rem.data_dir, resolve_range(period="all", tz="UTC"))
    assert numbers["project_check"]["flaky"] == 1
    assert numbers["project_check"]["failed_first"] == 0
    assert numbers["first_pass"] == 1


def test_a_flake_in_a_file_the_change_edited_reaches_the_reviewer_by_name(
    board,  # noqa: F811
    monkeypatch,
):
    """Self-work report, 21 November afternoon: a flaky pass used to read as a plain pass even
    when the failing test was one the change edited, which is how a race it brought in
    would land."""
    from tests.unit.test_rem_kanban import add, at, settle, tick
    from tests.unit.test_ticket_timeline import latest_run

    rem, fake, _ = board
    root = flaky_project(rem.data_dir.parent / "project")
    decide(rem, root)
    monkeypatch.setattr(
        RemHome, "_work_paths", lambda self, run, folder: ["src/clock.py", "tests/test_clock.py"]
    )
    add(rem, "Flaky in its own test")
    at(rem, 15)
    tick(rem)
    settle(rem, "Flaky in its own test")
    run = latest_run(rem, "Flaky in its own test")
    assert run["project_check"]["flaky_touches"] == ["tests/test_clock.py"]
    reviews = [
        c["agent_instructions"]
        for c in fake.calls
        if "independent read-only reviewer" in c["agent_instructions"]
        and run["verification_thread_id"] in c["conversation_id"]
    ]
    receipt = review_payload(reviews[0], "raticode_check")
    assert receipt["flaky"] is True
    assert receipt["flaky_in_changed_files"] == ["tests/test_clock.py"]
    assert receipt["summary"].endswith(
        "; the first failure pointed at tests/test_clock.py, which this change edited."
    )
    assert "flaky_in_changed_files lists files this change edited" in reviews[0]
    assert "report a should_fix finding" in reviews[0]


def test_flaky_touches_finds_a_failing_tests_file_or_an_edited_file_its_output_names():
    output = (
        "FAILED tests/test_clock.py::test_racy - AssertionError\n"
        '  File "/work/app/src/gofer/clock.py", line 12, in tick\n'
        "ERROR tests/test_other.py::test_db\n"
    )
    tests = ["tests/test_clock.py::test_racy", "tests/test_other.py::test_db"]
    changed = ["README.md", "src/gofer/clock.py", "tests/test_clock.py", "lock.py", "clock.py"]
    assert checks.flaky_touches(tests, changed, output, "/work/app") == [
        "src/gofer/clock.py",
        "tests/test_clock.py",
    ]
    assert checks.flaky_touches(tests, ["src/unrelated.py"], output) == []
    assert checks.flaky_touches([], [], output) == []
    plain = {"command": "make", "folder": "/w", "head": "a" * 40, "seconds": 3, "passed": True}
    assert checks.receipt({**plain, "flaky": True})["flaky_in_changed_files"] == []
    assert checks.summary({**plain, "flaky": True}).endswith("failed (flaky).")


def test_a_failure_that_repeats_on_the_same_commit_goes_back_once_and_is_reused(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root, _ = committed_project(tmp_path)
    git(root, "rm", "-q", "fixed.txt")
    git(root, "commit", "-qm", "break it")
    commit = git(root, "rev-parse", "HEAD")
    home.configure({"provider": "claude_code", "resources": RESOURCES})
    item = decide(home, root)
    turns: list[int] = []
    source, calls = source_for(root, fix_on_repair=False)

    async def counting(**kwargs: Any):
        if "independent read-only reviewer" not in kwargs["agent_instructions"]:
            turns.append(runs_of(root))
        async for event in source(**kwargs):
            yield event

    home.source = counting
    task = home.create_task({"request_id": "confirmed", **recipe(root)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=20)
    assert run["state"] == "blocked", run
    # Two runs on the first commit (the failure and its rerun), then one send-back; the
    # rechecks of that unchanged commit reuse the confirmed failure and never run it again.
    assert turns[:2] == [0, 2] and runs_of(root) == 2
    assert calls["review"] == []
    repair = calls["work"][1]["messages"][-1]["body"]
    assert f"Raticode ran this project's check ({item['id']}) itself" in repair
    assert "FAIL: fixed.txt is missing" in repair
    with home.store.read() as db:
        stored = home.store.get(db, "runs", run["id"])
    check = stored["project_check"]
    assert check["confirmed"] and not check["passed"] and check["head"] == commit
    assert not check.get("flaky") and not stored.get("project_check_flakes")
    assert "failed in" in check["summary"] and "again on a rerun" in check["summary"]
    # The work broke the check, not the command: one failure counted, none toward asking again.
    decided = next(i for i in home.decisions(str(root))["items"] if i["id"] == item["id"])
    assert decided["failures"] == 0 and decided["status"] == "confirmed"


def test_only_a_confirmed_failure_or_a_pass_stands_for_a_recheck():
    passed = {"passed": True, "exit_code": 0}
    once = {"passed": False, "exit_code": 1}
    confirmed = {**once, "confirmed": True}
    missing = {"passed": False, "exit_code": 127}
    assert checks.reusable(passed) and checks.reusable(confirmed) and checks.reusable(missing)
    assert not checks.reusable(once)
    # Only a command that exited by itself with a failing code is rerun.
    assert checks.may_rerun(once)
    for result in (
        passed,
        missing,
        {"passed": False, "exit_code": 126},
        {"passed": False, "exit_code": -9, "detail": "Setup ran past its time limit"},
        {"passed": False, "exit_code": None},
        {"passed": False, "skipped": "no sandbox"},
    ):
        assert not checks.may_rerun(result), result
    flaky = checks.settle({**once, "output": "FAILED a::b"}, {**passed, "output": "ok"})
    assert flaky["flaky"] and flaky["passed"] and flaky["first_output"] == "FAILED a::b"
    assert flaky["output"] == "ok"
    again = checks.settle(once, {**once, "output": "still"})
    assert again["confirmed"] and not again["passed"] and "flaky" not in again


def test_a_pass_that_follows_a_file_the_failed_run_left_shows_that_file(tmp_path: Path):
    """Nightly audit, 22 November: a check made to fail once, leave a marker and pass on the
    rerun counted as a plain flaky pass, and the reviewer saw only the passing output."""
    import subprocess

    root = flaky_project(tmp_path / "project")
    (root / ".gitignore").write_text("ran.log\n")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    for args in (
        ["init", "-q"],
        ["add", "check.sh", ".gitignore"],
        ["-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-qm", "c"],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, env=env)
    before = checks.leftovers(root)
    first = subprocess.run(["sh", "check.sh"], cwd=root, capture_output=True, text=True)
    left = sorted(checks.leftovers(root) - before)
    assert left == ["flaked.txt", "ran.log"]
    again = subprocess.run(["sh", "check.sh"], cwd=root, capture_output=True, text=True)
    result = checks.settle(
        {"passed": False, "exit_code": first.returncode, "output": first.stdout},
        {"passed": True, "exit_code": again.returncode, "output": again.stdout},
    )
    result.update(command="sh check.sh", folder=str(root), first_run_left=left)
    shown = checks.receipt(result)
    assert shown["flaky"] and shown["first_run_left"] == ["flaked.txt", "ran.log"]
    assert f"FAILED {FLAKY_TEST}" in shown["first_run_output_tail"]
    assert "first_run_left" in checks.REVIEW_GUIDANCE
    # Outside git nothing is listed, and a plain pass shows no first run.
    assert checks.leftovers(tmp_path) == set()
    plain = checks.receipt({"passed": True, "command": "x", "folder": str(root)})
    assert plain["first_run_output_tail"] == "" and plain["first_run_left"] == []


# A fresh worktree of the commit that lands -----------------------------------------------

FEATURE_CHECK = f'"{sys.executable}" -B test_feature.py'


def task_worktree(tmp_path: Path, *, commit_helper: bool = False) -> Path:
    """A git folder whose committed test imports ``helper.py``, which is left untracked
    unless ``commit_helper``."""
    root = tmp_path / "work"
    root.mkdir()
    git(root, "init", "-q")
    (root / "test_feature.py").write_text(
        "from helper import answer\nassert answer() == 42\nprint('feature ok')\n"
    )
    git(root, "add", "test_feature.py")
    git(root, "commit", "-qm", "Add the feature's test")
    (root / "helper.py").write_text("def answer():\n    return 42\n")
    if commit_helper:
        git(root, "add", "helper.py")
        git(root, "commit", "-qm", "Add the helper")
    return root


def check_worktrees(repo: Path, data: Path) -> list[str]:
    """The repository's worktrees in the data folder's run-scratch."""
    scratch = os.path.realpath(data / checks.SCRATCH)
    return [
        line
        for line in git(repo, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ") and scratch in line
    ]


def check_folders(data: Path) -> list[str]:
    return sorted(p.name for p in (data / checks.SCRATCH).glob(f"{checks.BOX_PREFIX}*"))


def test_a_file_the_worker_didnt_commit_fails_the_check_in_a_fresh_worktree(tmp_path: Path):
    root = task_worktree(tmp_path)
    data = tmp_path / "data"
    commit = git(root, "rev-parse", "HEAD")
    # In the task's worktree the untracked helper makes the same command pass.
    in_place = checks.run(FEATURE_CHECK, root, data_dir=data)
    assert in_place["passed"] and "feature ok" in in_place["output"], in_place
    result = checks.run_check(FEATURE_CHECK, root, data_dir=data)
    assert not result["passed"], result
    assert result["ran_in"] == checks.FRESH_WORKTREE and result["head"] == commit
    assert "No module named 'helper'" in result["output"]
    assert result["folder"] == str(root) and result["ran_folder"] != str(root)
    # The reviewer and the worker are told where it ran and why that matters.
    assert checks.receipt(result)["ran_in"] == checks.FRESH_WORKTREE
    assert f"in a fresh worktree of commit {commit[:12]} from {root}" in checks.summary(result)
    assert "a file you didn't commit isn't there" in checks.feedback(result, "D-3")
    assert "ran_in fresh_worktree" in checks.REVIEW_GUIDANCE
    # Committed, it passes there too.
    git(root, "add", "helper.py")
    git(root, "commit", "-qm", "Add the helper")
    fixed = checks.run_check(FEATURE_CHECK, root, data_dir=data)
    assert fixed["passed"] and fixed["ran_in"] == checks.FRESH_WORKTREE, fixed
    assert fixed["head"] == git(root, "rev-parse", "HEAD") != commit
    assert check_worktrees(root, data) == [] and check_folders(data) == []


@pytest.mark.parametrize("outcome", ["passed", "failed", "cancelled"])
def test_no_check_worktree_or_folder_is_left_after_a_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
):
    root = task_worktree(tmp_path, commit_helper=True)
    data = tmp_path / "data"
    command = {
        "passed": "echo made > made.txt",
        "failed": "echo made > made.txt; exit 1",
        "cancelled": "echo made > made.txt; sleep 30",
    }[outcome]
    cancel = threading.Event()
    if outcome == "cancelled":
        threading.Timer(1.0, cancel.set).start()
    seen: dict[str, list[str]] = {}
    real = checks._run_in

    def watched(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen["worktrees"] = check_worktrees(root, data)
        seen["folders"] = check_folders(data)
        return real(*args, **kwargs)

    monkeypatch.setattr(checks, "_run_in", watched)
    result = checks.run_check(command, root, data_dir=data, cancel=cancel)
    # It ran in a worktree of its own, in a check-<id> folder...
    assert result["ran_in"] == checks.FRESH_WORKTREE, result
    assert len(seen["worktrees"]) == 1 and len(seen["folders"]) == 1
    assert result["passed"] is (outcome == "passed")
    if outcome == "cancelled":
        assert result["seconds"] < 20
    # ...and neither is left afterwards, nor anything it wrote in the task's worktree.
    assert check_worktrees(root, data) == []
    assert check_folders(data) == []
    assert not (root / "made.txt").exists()


def test_leftover_check_worktrees_are_swept_at_the_next_check_and_at_startup(tmp_path: Path):
    from gofer.ui.scratch_cleanup import clean_stale_scratch

    root = task_worktree(tmp_path, commit_helper=True)
    data = tmp_path / "data"

    def left_behind() -> Path:
        """What a backend stopped in the middle of a check leaves."""
        box = checks._new_box(data)
        checks._make_fresh(root, box, data_dir=data, source=root, cancel=None)
        return box

    left_behind()
    assert len(check_worktrees(root, data)) == 1
    result = checks.run_check("true", root, data_dir=data)
    assert result["passed"] and result["ran_in"] == checks.FRESH_WORKTREE
    assert check_worktrees(root, data) == [] and check_folders(data) == []
    left_behind()
    gone = left_behind()
    # A worktree folder already deleted still has its record in the repository.
    import shutil

    shutil.rmtree(gone / checks.BOX_WORKTREE)
    assert len(check_worktrees(root, data)) == 2
    # The general scratch clean-up leaves them to the checks' own sweep.
    clean_stale_scratch(data, now=10**10)
    assert len(check_folders(data)) == 2
    checks.start_sweep(data).join(30)
    assert check_worktrees(root, data) == [] and check_folders(data) == []
    server = Path(checks.__file__).with_name("server.py").read_text()
    assert "rem_project_checks.start_sweep(server.data_dir)" in server


def test_outside_git_the_check_runs_in_place_and_says_why(tmp_path: Path):
    data = tmp_path / "data"
    plain = tmp_path / "plain"
    plain.mkdir()
    result = checks.run_check("echo here > ran.txt", plain, data_dir=data)
    assert result["passed"] and (plain / "ran.txt").read_text() == "here\n", result
    assert result["ran_in"] == checks.TASK_WORKTREE
    assert result["ran_in_reason"].startswith("the folder isn't in a git repository")
    shown = checks.receipt(result)
    assert shown["ran_in"] == checks.TASK_WORKTREE
    assert shown["ran_in_reason"] == result["ran_in_reason"]
    assert "(not in a fresh worktree: the folder isn't in a git repository" in (
        checks.summary(result)
    )
    assert "ran_in_reason says why" in checks.REVIEW_GUIDANCE
    # A repository with no commit yet has nothing to check out.
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-q")
    unborn = checks.run_check("true", empty, data_dir=data)
    assert unborn["passed"] and unborn["ran_in"] == checks.TASK_WORKTREE
    assert unborn["ran_in_reason"].startswith("its commit can't be read")
    assert check_folders(data) == []


def test_the_approved_setup_runs_in_the_fresh_worktree_and_a_failing_one_falls_back(
    tmp_path: Path,
):
    from gofer.ui import worktree_setup

    root = tmp_path / "work"
    (root / ".raticode").mkdir(parents=True)
    git(root, "init", "-q")
    (root / ".gitignore").write_text("deps\n")
    setup_file = root / worktree_setup.CONFIG_PATH
    setup_file.write_text(json.dumps({"link": ["deps"]}))
    git(root, "add", ".")
    git(root, "commit", "-qm", "Set up")
    (root / "deps").mkdir()
    (root / "deps" / "lib.txt").write_text("installed\n")
    data = tmp_path / "data"

    def approve() -> None:
        state = worktree_setup.status(data, root)
        worktree_setup.approve(data, root, state["file"]["sha256"])

    approve()
    linked = checks.run_check("cat deps/lib.txt", root, data_dir=data)
    assert linked["passed"] and "installed" in linked["output"], linked
    assert linked["ran_in"] == checks.FRESH_WORKTREE
    assert linked["setup"].startswith("Project setup finished")
    setup_file.write_text(json.dumps({"link": ["deps", "missing"]}))
    git(root, "commit", "-qam", "Break the setup")
    approve()
    fallback = checks.run_check("cat deps/lib.txt", root, data_dir=data)
    assert fallback["passed"] and fallback["ran_in"] == checks.TASK_WORKTREE, fallback
    assert fallback["ran_in_reason"].startswith("the project's worktree setup didn't finish")
    assert check_worktrees(root, data) == [] and check_folders(data) == []


def test_a_check_copy_never_runs_setup_commands_on_the_workers_committed_files(
    tmp_path: Path, proving_python: None
):
    """An approved setup that runs ``./setup.sh`` would run the worker's own committed
    script outside the sandbox, so a check's fresh worktree never runs setup commands."""
    from gofer.ui import rem_proving_test as proving
    from gofer.ui import worktree_setup

    root, _ = calc_repo(tmp_path, broken=True)
    (root / ".raticode").mkdir()
    (root / worktree_setup.CONFIG_PATH).write_text(json.dumps({"setup": "sh ./setup.sh"}))
    (root / "setup.sh").write_text("true\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "Set up")
    git(tmp_path / "dev-home", "merge", "-q", "--ff-only", "test")
    data = tmp_path / "data"
    state = worktree_setup.status(data, root)
    worktree_setup.approve(data, root, state["file"]["sha256"])
    base = git(root, "rev-parse", "dev")
    folder, _ = calc_work(root, tmp_path, fix=True)
    marker = tmp_path / "outside.txt"
    # The worker changes only the script the approved setup runs, not the setup file.
    (folder / "setup.sh").write_text(f"echo planted > {marker}\n")
    git(folder, "commit", "-qam", "Plant the setup script")
    result = checks.run_check("true", folder, data_dir=data, source=root)
    assert not marker.exists(), "the worker's setup script ran outside the sandbox"
    assert result["passed"] and result["ran_in"] == checks.TASK_WORKTREE, result
    assert "runs commands" in result["ran_in_reason"]
    assert result["sandboxed"] is True
    pair = proving.run(PROVING, folder, data_dir=data, base=base, source=root)
    assert not marker.exists(), "the worker's setup script ran for the proving test"
    assert not pair["ran"] and "runs commands" in pair["not_run_reason"], pair
    assert check_worktrees(root, data) == [] and check_folders(data) == []


def test_a_file_the_failed_first_run_left_cant_decide_its_rerun(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = tmp_path / "proj"
    root.mkdir()
    log = tmp_path / "ran.log"
    # Fails the first time; passes only where an earlier run left its marker.
    (root / "check.sh").write_text(
        f"echo run >> {log}\n"
        'if [ -f flaked.txt ]; then echo "= 3 passed ="; exit 0; fi\n'
        "touch flaked.txt\n"
        f'echo "FAILED {FLAKY_TEST} - AssertionError"; exit 1\n'
    )
    (root / ".gitignore").write_text("flaked.txt\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-qm", "start")
    home.configure({"provider": "claude_code", "resources": RESOURCES})
    decide(home, root)
    source, calls = source_for(root, fix_on_repair=False)
    home.source = source
    task = home.create_task({"request_id": "marker", **recipe(root)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=20)
    # The rerun had its own fresh worktree, so the marker wasn't there: a repeated failure.
    with home.store.read() as db:
        check = home.store.get(db, "runs", run["id"])["project_check"]
    assert check["confirmed"] and not check["passed"] and not check.get("flaky"), check
    assert check["ran_in"] == checks.FRESH_WORKTREE and not check.get("first_run_left")
    assert len(log.read_text().splitlines()) == 2 and not (root / "flaked.txt").exists()
    assert calls["review"] == []
    assert check_folders(home.data_dir) == []


def test_the_check_runs_in_a_fresh_worktree_inside_its_sandbox(real_sandbox: None, tmp_path: Path):
    root = task_worktree(tmp_path, commit_helper=True)
    data = tmp_path / "data"
    result = checks.run_check(
        f'{FEATURE_CHECK} && echo x > written.txt && echo x > "$TMPDIR/t" && echo WROTE-OK',
        root,
        data_dir=data,
    )
    assert result["sandboxed"] and result["ran_in"] == checks.FRESH_WORKTREE, result
    assert result["passed"] and "WROTE-OK" in result["output"], result
    assert not (root / "written.txt").exists()
    assert check_worktrees(root, data) == [] and check_folders(data) == []


# Raticode runs the proving test itself: before the change and after it ----------------------

PROVING = "tests/test_calc.py::test_answer"
CALC_TEST = "from calc import answer\n\n\ndef test_answer():\n    assert answer() == 42\n"


@pytest.fixture
def proving_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """The proving test's pytest runs with this interpreter, which has pytest."""
    from gofer.ui import rem_proving_test as proving

    monkeypatch.setattr(proving, "FALLBACK_PYTHON", sys.executable)


def calc_repo(tmp_path: Path, *, broken: bool) -> tuple[Path, Path]:
    """A project on ``test`` whose ``dev`` is in its own worktree; ``answer()`` is 41 on
    dev when ``broken``. Returns the project and dev's folder."""
    root = tmp_path / "calc"
    root.mkdir()
    git(root, "init", "-q", "-b", "test")
    (root / "calc.py").write_text(f"def answer():\n    return {41 if broken else 42}\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "start")
    git(root, "branch", "dev")
    home_dir = tmp_path / "dev-home"
    git(root, "worktree", "add", "-q", str(home_dir), "dev")
    return root, home_dir


def calc_work(root: Path, tmp_path: Path, *, fix: bool) -> tuple[Path, list[str]]:
    """A worker's branch off dev that adds the proving test (and fixes calc.py when
    ``fix``), committed; the folder and the commands that made it."""
    folder = tmp_path / "wt" / "fix-answer"
    git(root, "worktree", "add", "-q", "-b", "fix/answer", str(folder), "dev")
    (folder / "tests").mkdir()
    (folder / "tests" / "test_calc.py").write_text(CALC_TEST)
    if fix:
        (folder / "calc.py").write_text("def answer():\n    return 42\n")
    git(folder, "add", ".")
    git(folder, "commit", "-qm", "fix: answer is 42")
    return folder, [
        f"git -C {root} worktree add -b fix/answer {folder} dev",
        f'cd {folder} && git add . && git commit -m "fix: answer is 42"',
    ]


def test_a_proving_test_name_is_read_from_the_result_and_only_runnable_ones_run():
    from gofer.ui import rem_proving_test as proving

    assert proving.named_test(f"Done.\nProving test: `{PROVING}`\n") == PROVING
    assert proving.named_test(f"**Proving test:** {PROVING}.") == PROVING
    assert proving.named_test("Proving test: `node --test web/a.test.js`") == "web/a.test.js"
    assert proving.named_test("Not testable here because it needs a phone") is None
    assert proving.target(PROVING) == {
        "kind": "pytest",
        "file": "tests/test_calc.py",
        "test": PROVING,
    }
    assert proving.target("web/a.test.js")["kind"] == "node"  # type: ignore[index]
    for name in ("test_answer", "../x.py::t", "/abs/x.py::t", "-k::t", "x.txt::t", "x.py::"):
        assert isinstance(proving.target(name), str), name
    assert proving.is_test_path("tests/data/case.json") and proving.is_test_path("a/test_b.py")
    assert proving.is_test_path("web/src/a.test.ts") and not proving.is_test_path("src/calc.py")


def test_a_proving_test_that_fails_on_its_base_and_passes_on_the_work_is_proven(
    tmp_path: Path, proving_python: None
):
    from gofer.ui import rem_proving_test as proving

    root, _ = calc_repo(tmp_path, broken=True)
    folder, _ = calc_work(root, tmp_path, fix=True)
    data = tmp_path / "data"
    base = git(root, "rev-parse", "dev")
    result = proving.run(PROVING, folder, data_dir=data, base=base)
    assert result["ran"], result
    assert result["base_commit"] == base and result["commit"] == git(folder, "rev-parse", "HEAD")
    # The base got the new test file but not the fix, so the test failed there.
    assert result["base_test_files"] == ["tests/test_calc.py"]
    assert (
        result["before"]["outcome"] == "failed" and "assert 41 == 42" in result["before"]["output"]
    )
    assert result["after"]["outcome"] == "passed" and "1 passed" in result["after"]["output"]
    assert result["failed_before"] and result["passes_after"]
    assert result["status"] == "fails before the change and passes after it"
    assert check_worktrees(root, data) == [] and check_folders(data) == []
    # A test whose file the work never committed isn't run at all.
    missing = proving.run("tests/test_other.py::test_x", folder, data_dir=data, base=base)
    assert not missing["ran"] and "isn't in the work's commit" in missing["not_run_reason"]


def proving_flow(home: RemHome, tmp_path: Path, *, broken: bool):  # noqa: F811
    """A develop task under the worktree skill that lands a branch naming PROVING."""
    from tests.unit.test_rem_home_landing import decide_branch
    from tests.unit.test_rem_thread_parity import thread_defaults

    root, dev_home = calc_repo(tmp_path, broken=broken)
    home.configure({"provider": "claude_code", "resources": thread_defaults(tmp_path)})
    decide_branch(home, root)
    calls: dict[str, list[dict[str, Any]]] = {"work": [], "review": []}

    async def source(**kwargs: Any):
        if "independent read-only reviewer" in kwargs["agent_instructions"]:
            calls["review"].append(kwargs)
            for event in passing_review():
                yield event
            return
        calls["work"].append(kwargs)
        _, commands = calc_work(root, tmp_path, fix=broken)
        git(dev_home, "merge", "-q", "--ff-only", "fix/answer")
        commands.append(f"git -C {dev_home} merge --ff-only fix/answer")
        for command in commands:
            yield shell(command)
        yield {
            "type": "final",
            "message": {"body": f"Fixed and landed.\nProving test: `{PROVING}`"},
        }

    home.source = source
    task = home.create_task({"request_id": f"prove-{broken}", **recipe(root)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=60)
    return root, run, calls


def test_the_reviewer_sees_raticodes_failing_before_and_passing_after_runs(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
    proving_python: None,
):
    from gofer.ui.rem_home import REVIEW_PROVING_TEST

    root, run, calls = proving_flow(home, tmp_path, broken=True)
    assert run["state"] == "succeeded", run
    review = calls["review"][0]["agent_instructions"]
    shown = review_payload(review, "raticode_proving_test")
    assert shown["produced_by"] == "Raticode, not the worker"
    assert shown["test"] == PROVING and shown["ran"] is True
    assert shown["failed_before"] is True and shown["passes_after"] is True
    assert shown["status"] == "fails before the change and passes after it"
    # Both runs, each with its commit, exit code and output tail. The work already
    # landed on dev, so its base comes from dev's reflog, not dev's tip.
    start = git(root, "rev-list", "--max-parents=0", "HEAD")
    assert shown["before"]["commit"] == start
    assert shown["before"]["outcome"] == "failed" and shown["before"]["exit_code"] == 1
    assert "assert 41 == 42" in shown["before"]["output_tail"]
    assert shown["before"]["test_files_added"] == ["tests/test_calc.py"]
    assert shown["after"]["commit"] == git(root, "rev-parse", "fix/answer")
    assert shown["after"]["outcome"] == "passed" and shown["after"]["exit_code"] == 0
    assert "1 passed" in shown["after"]["output_tail"]
    assert "fails before the change and passes after it" in shown["summary"]
    # The reviewer is told to trust the pair over the worker's claim.
    assert REVIEW_PROVING_TEST in review
    assert "trust that pair over the worker's own claim" in review
    with home.store.read() as db:
        stored = home.store.get(db, "runs", run["id"])["proving_test"]
    assert stored["failed_before"] and stored["passes_after"]
    assert check_folders(home.data_dir) == []


def test_a_proving_test_that_passes_on_its_base_is_shown_as_not_failing_before(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
    proving_python: None,
):
    _, run, calls = proving_flow(home, tmp_path, broken=False)
    assert run["state"] == "succeeded", run
    shown = review_payload(calls["review"][0]["agent_instructions"], "raticode_proving_test")
    assert shown["ran"] is True and shown["failed_before"] is False
    assert shown["before"]["outcome"] == "passed" and shown["passes_after"] is True
    assert shown["status"] == "did not fail before the change"
    assert "It did not fail before the change." in shown["summary"]


def test_a_proving_test_raticode_cant_run_is_recorded_as_not_run_with_why(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    from gofer.ui import rem_proving_test as proving

    root, _ = committed_project(tmp_path)
    home.configure({"provider": "claude_code", "resources": RESOURCES})

    async def source(**kwargs: Any):
        if "independent read-only reviewer" in kwargs["agent_instructions"]:
            calls.append(kwargs)
            for event in passing_review():
                yield event
            return
        yield shell("ls")
        yield {"type": "final", "message": {"body": "Done.\nProving test: test_answer"}}

    calls: list[dict[str, Any]] = []
    home.source = source
    task = home.create_task({"request_id": "cant", **recipe(root)})["task"]
    wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=20)
    shown = review_payload(calls[0]["agent_instructions"], "raticode_proving_test")
    assert shown["ran"] is False and shown["status"] == proving.NOT_RUN == "not run by Raticode"
    assert "isn't a pytest node ID" in shown["not_run_reason"]
    assert "not run by Raticode" in shown["summary"]
    assert "not_run_reason says why" in calls[0]["agent_instructions"]


FAKE_PYTHON = "#!/bin/sh\necho '1 passed'\nexit 0\n"


def fake_venv(folder: Path) -> None:
    """A ``.venv/bin/python`` that claims every test passed."""
    python = folder / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(FAKE_PYTHON)
    python.chmod(0o755)


def test_a_python_the_worker_put_in_its_worktree_never_runs_the_proving_test(
    tmp_path: Path, proving_python: None
):
    from gofer.ui import rem_proving_test as proving

    root, _ = calc_repo(tmp_path, broken=True)
    base = git(root, "rev-parse", "dev")
    # The work adds the test but doesn't fix calc.py, so the test fails after it too.
    folder, _ = calc_work(root, tmp_path, fix=False)
    data = tmp_path / "data"
    fake_venv(folder)
    planted = proving.run(PROVING, folder, data_dir=data, base=base)
    assert planted["ran"], planted
    assert planted["after"]["outcome"] == "failed", planted["after"]
    assert not planted["passes_after"]
    assert planted["before"]["command"] == planted["after"]["command"]
    assert str(folder) not in planted["after"]["command"]
    # Committed, it would reach the fresh checkout, so the pair isn't made at all.
    git(folder, "add", "-f", ".venv")
    git(folder, "commit", "-qm", "Add an environment")
    committed = proving.run(PROVING, folder, data_dir=data, base=base)
    assert not committed["ran"] and ".venv" in committed["not_run_reason"], committed
    assert check_worktrees(root, data) == [] and check_folders(data) == []


def test_the_proving_test_runs_inside_the_check_sandbox(
    real_sandbox: None, tmp_path: Path, proving_python: None
):
    from gofer.ui import rem_proving_test as proving

    root, _ = calc_repo(tmp_path, broken=True)
    folder, _ = calc_work(root, tmp_path, fix=True)
    data = tmp_path / "data"
    result = proving.run(PROVING, folder, data_dir=data, base=git(root, "rev-parse", "dev"))
    assert result["ran"] and result["failed_before"] and result["passes_after"], result
    assert check_worktrees(root, data) == [] and check_folders(data) == []
