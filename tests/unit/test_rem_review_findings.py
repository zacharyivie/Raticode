"""The review's findings: two axes (spec, standards), each with a severity.

A blocker sends the work back as a repair, a should-fix sends it back once per run, and a
note never blocks: it is stored on the run. The review prompt names the project's
standards file by path without pasting it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import rem_review_findings as findings
from gofer.ui.rem_home import RemHome
from tests.unit.test_rem_home import home, recipe, wait_run  # noqa: F401
from tests.unit.test_rem_home_landing import shell

RESOURCES = {"shell": True, "web": False, "agents": False, "browser": False, "skills": []}


def finding(severity: str, axis: str = "standards", **patch: str) -> dict[str, str]:
    return {
        "axis": axis,
        "severity": severity,
        "file": "src/app.py",
        "explanation": f"A {severity} about {axis}.",
        **patch,
    }


def review(found: list[dict[str, str]]) -> list[dict[str, Any]]:
    """A reviewer that inspected the work, passed every criterion and found ``found``."""
    return [
        {
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
        },
        {
            "type": "final",
            "message": {
                "body": json.dumps(
                    {
                        "criteria": [{"id": "check", "result": "pass", "evidence": "Saw it"}],
                        "findings": found,
                        "feedback": "",
                    }
                )
            },
        },
    ]


def stored_run(home: RemHome) -> dict[str, Any]:  # noqa: F811
    with home.store.read() as db:
        rows = db.execute("SELECT body FROM runs").fetchall()
    assert len(rows) == 1
    run: dict[str, Any] = json.loads(rows[0][0])
    return run


def wire(
    home: RemHome,  # noqa: F811
    reviews: Callable[[int], list[dict[str, str]]],
) -> dict[str, list[Any]]:
    """Fake worker and reviewer. ``reviews(n)`` gives the n-th review's findings (from 0).

    Each worker turn after the first records the run as it stood when the repair began.
    """
    calls: dict[str, list[Any]] = {"work": [], "review": [], "repairs": []}

    async def source(**kwargs: Any):
        if "independent read-only reviewer" in kwargs["agent_instructions"]:
            calls["review"].append(kwargs)
            for event in review(reviews(len(calls["review"]) - 1)):
                yield event
            return
        calls["work"].append(kwargs)
        if len(calls["work"]) > 1:
            calls["repairs"].append(stored_run(home))
        yield shell("ls")
        yield {"type": "final", "message": {"body": f"Turn {len(calls['work'])} done."}}

    home.source = source
    return calls


def start(home: RemHome, root: Path, **patch: Any) -> dict[str, Any]:  # noqa: F811
    home.configure({"provider": "claude_code", "resources": RESOURCES})
    task = home.create_task({"request_id": "findings", **recipe(root, **patch)})["task"]
    return wait_run(home, task["id"], {"succeeded", "blocked"}, timeout=20)


def folder(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    return root


def test_a_note_never_blocks_and_is_stored_on_the_run(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    note = finding("note", explanation="The helper name could say what it returns.")
    calls = wire(home, lambda n: [note])
    run = start(home, folder(tmp_path))
    assert run["state"] == "succeeded", run
    assert len(calls["work"]) == 1 and len(calls["review"]) == 1
    stored = stored_run(home)
    assert stored["review_notes"] == [note]
    assert stored["repair_count"] == 0
    # The task sheet gets them with the run.
    assert run["review_notes"] == [note]


def test_a_blocker_sends_the_work_back_as_a_repair(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    blocker = finding("blocker", explanation="It writes the token to the log file.")
    calls = wire(home, lambda n: [blocker] if n == 0 else [])
    run = start(home, folder(tmp_path))
    assert run["state"] == "succeeded", run
    assert len(calls["work"]) == 2 and len(calls["review"]) == 2
    repairing = calls["repairs"][0]
    assert repairing["state"] == "repairing" and repairing["repair_count"] == 1
    assert "It writes the token to the log file." in repairing["review_feedback"]
    assert "[blocker, standards] in src/app.py" in repairing["review_feedback"]
    # The worker is told the finding.
    assert "It writes the token to the log file." in calls["work"][1]["messages"][-1]["body"]
    assert stored_run(home)["review_notes"] == []


def test_a_blocker_that_stays_blocks_the_run_after_two_repairs(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    blocker = finding("blocker", axis="spec", explanation="The export button does nothing.")
    calls = wire(home, lambda n: [blocker])
    run = start(home, folder(tmp_path))
    assert run["state"] == "blocked", run
    assert len(calls["work"]) == 3
    assert "The review still has blocking findings." in run["summary_short"]


def test_a_should_fix_goes_back_once_then_stays_as_a_note(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    should = finding("should-fix", explanation="Name the magic number.")
    calls = wire(home, lambda n: [should])
    run = start(home, folder(tmp_path))
    assert run["state"] == "succeeded", run
    assert len(calls["work"]) == 2 and len(calls["review"]) == 2
    assert "Name the magic number." in calls["repairs"][0]["review_feedback"]
    stored = stored_run(home)
    assert stored["should_fix_returned"] is True
    assert stored["review_notes"] == [{**should, "severity": "should_fix"}]


def test_notes_beside_a_failed_criterion_dont_add_to_the_repair(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    note = finding("note")
    rounds = {"n": 0}

    async def source(**kwargs: Any):
        if "independent read-only reviewer" in kwargs["agent_instructions"]:
            rounds["n"] += 1
            events = review([note])
            if rounds["n"] == 1:
                body = json.loads(events[-1]["message"]["body"])
                body["criteria"][0].update(result="fail", evidence="Nothing changed")
                body["feedback"] = "Make the change."
                events[-1]["message"]["body"] = json.dumps(body)
            for event in events:
                yield event
            return
        yield shell("ls")
        yield {"type": "final", "message": {"body": f"Turn {rounds['n']} done."}}

    home.source = source
    run = start(home, folder(tmp_path))
    assert run["state"] == "succeeded", run
    stored = stored_run(home)
    # One repair for the failed criterion; the note never counted.
    assert stored["repair_count"] == 1
    assert stored["review_notes"] == [note]


def test_the_review_names_contributing_md_by_path_when_there_is_no_coding_standards(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = folder(tmp_path)
    (root / "CONTRIBUTING.md").write_text("Keep functions short. SECRET-RULE-TEXT\n")
    (root / "AGENTS.md").write_text("Agent notes.\n")
    calls = wire(home, lambda n: [])
    start(home, root)
    prompt = calls["review"][0]["agent_instructions"]
    assert f"The project's written standards are in {root / 'CONTRIBUTING.md'}." in prompt
    assert "judge the diff this task made against it as a second axis" in prompt
    # Named, not pasted, and the file further down the list isn't named.
    assert "SECRET-RULE-TEXT" not in prompt
    assert str(root / "AGENTS.md") not in prompt
    assert "findings:[{axis:spec|standards,severity:blocker|should_fix|note," in prompt


def test_coding_standards_wins_and_agents_md_is_the_last_choice(tmp_path: Path):
    assert findings.standards_file(tmp_path) is None
    (tmp_path / "AGENTS.md").write_text("a\n")
    assert findings.standards_file(tmp_path) == tmp_path / "AGENTS.md"
    (tmp_path / "CONTRIBUTING.md").write_text("c\n")
    assert findings.standards_file(tmp_path) == tmp_path / "CONTRIBUTING.md"
    (tmp_path / "CODING_STANDARDS.md").write_text("s\n")
    assert findings.standards_file(tmp_path) == tmp_path / "CODING_STANDARDS.md"


def test_research_reviews_have_no_standards_axis(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = folder(tmp_path)
    (root / "CODING_STANDARDS.md").write_text("Rules.\n")
    calls = wire(home, lambda n: [])
    # A maintain task changes files, so it gets the axis.
    start(home, root, type="maintain", title="Tidy")
    assert str(root / "CODING_STANDARDS.md") in calls["review"][0]["agent_instructions"]
    guidance = findings.standards_guidance(root / "CODING_STANDARDS.md")
    assert home._standards_guidance({"recipe": recipe(root, type="research")}) == ""
    assert home._standards_guidance({"recipe": recipe(root)}) == guidance


def test_a_glossary_gets_one_pointer_line_for_the_worker_and_is_named_in_the_review(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = folder(tmp_path)
    (root / "GLOSSARY.md").write_text(
        "| term | meaning | name in code | words to avoid |\n"
        "| worker | GLOSSARY-SECRET-TEXT | worker | Employee |\n"
    )
    (root / "CONTEXT.md").write_text("Older list. CONTEXT-SECRET-TEXT\n")
    calls = wire(home, lambda n: [])
    start(home, root)
    work = calls["work"][0]["agent_instructions"]
    pointer = findings.glossary_pointer(root / "GLOSSARY.md")
    # Exactly one line names the file, and none of its text is copied.
    assert pointer.count("\n") == 1
    assert pointer.rstrip("\n") in work.splitlines()
    assert [line for line in work.splitlines() if "GLOSSARY.md" in line] == [pointer.rstrip("\n")]
    assert "GLOSSARY-SECRET-TEXT" not in work and "Employee" not in work
    # GLOSSARY.md wins over the older CONTEXT.md.
    assert "CONTEXT" not in work
    review_prompt = calls["review"][0]["agent_instructions"]
    assert f"The project's glossary of shared words is {root / 'GLOSSARY.md'}." in review_prompt
    assert "should_fix or note, never blocker" in review_prompt
    assert "GLOSSARY-SECRET-TEXT" not in review_prompt
    # The glossary is named even when the project has no standards file.
    assert "written standards are in" not in review_prompt


def test_a_standards_file_the_worker_wrote_is_flagged_and_a_linked_glossary_is_skipped(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    """Nightly audit, 22 November: the reviewer was told to judge the work against the
    standards file in the worker's own folder, which the worker can add or rewrite, and a
    glossary link could point the reviewer at a file the worker can't read."""
    import os
    import subprocess

    root = folder(tmp_path)
    (root / "AGENTS.md").write_text("Never skip tests.\n")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    for args in (
        ["init", "-q"],
        ["add", "AGENTS.md"],
        ["-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-qm", "c"],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, env=env)
    run = {"recipe": recipe(root)}
    committed = home._standards_guidance(run)
    assert str(root / "AGENTS.md") in committed and "the worker wrote that text" not in committed
    # An uncommitted CODING_STANDARDS.md hides AGENTS.md: it's named, and flagged as the
    # worker's own text.
    (root / "CODING_STANDARDS.md").write_text("Skipping tests is fine.\n")
    planted = home._standards_guidance(run)
    assert f"This change edited {root / 'CODING_STANDARDS.md'}, or it isn't committed" in planted
    assert "landing branch's version" in planted
    # A glossary that is a link is never named; a real CONTEXT.md is used.
    secret = tmp_path / "elsewhere.md"
    secret.write_text("not the worker's to read\n")
    (root / "GLOSSARY.md").symlink_to(secret)
    assert findings.glossary_file(root) is None
    (root / "CONTEXT.md").write_text("c\n")
    assert findings.glossary_file(root) == root / "CONTEXT.md"


def test_a_standards_link_that_stays_inside_the_project_is_still_named(tmp_path: Path):
    """Nightly audit, 23 November: skipping every link also dropped a project whose
    AGENTS.md links to docs/AGENTS.md. A link inside the project is named; one leading out
    of it isn't."""
    root = folder(tmp_path)
    (root / "docs").mkdir()
    (root / "docs" / "AGENTS.md").write_text("Rules.\n")
    (root / "AGENTS.md").symlink_to(Path("docs") / "AGENTS.md")
    assert findings.standards_file(root) == root / "AGENTS.md"
    outside = tmp_path / "outside.md"
    outside.write_text("not the project's\n")
    (root / "CONTRIBUTING.md").symlink_to(outside)
    # CONTRIBUTING.md would win over AGENTS.md, but it leads out of the project.
    assert findings.standards_file(root) == root / "AGENTS.md"


def test_context_md_is_the_glossary_when_there_is_no_glossary_md(tmp_path: Path):
    assert findings.glossary_file(tmp_path) is None
    (tmp_path / "CONTEXT.md").write_text("c\n")
    assert findings.glossary_file(tmp_path) == tmp_path / "CONTEXT.md"
    (tmp_path / "GLOSSARY.md").write_text("g\n")
    assert findings.glossary_file(tmp_path) == tmp_path / "GLOSSARY.md"
    assert findings.standards_guidance(None) == ""
    both = findings.standards_guidance(tmp_path / "AGENTS.md", tmp_path / "GLOSSARY.md")
    assert both.startswith(f"The project's written standards are in {tmp_path / 'AGENTS.md'}.")
    assert f"glossary of shared words is {tmp_path / 'GLOSSARY.md'}." in both


def test_without_a_glossary_the_worker_prompt_has_no_pointer(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    calls = wire(home, lambda n: [])
    start(home, folder(tmp_path))
    assert "shared words" not in calls["work"][0]["agent_instructions"]
    assert "glossary of shared words" not in calls["review"][0]["agent_instructions"]


def test_findings_are_checked_and_normalized():
    assert findings.parse(None) == []
    parsed = findings.parse(
        [{"axis": "Spec", "severity": "Should fix", "explanation": " Tidy it. ", "file": None}]
    )
    assert parsed == [
        {"axis": "spec", "severity": "should_fix", "file": "", "explanation": "Tidy it."}
    ]
    for bad in (
        "none",
        [{"axis": "style", "severity": "note", "explanation": "x"}],
        [{"axis": "spec", "severity": "nit", "explanation": "x"}],
        [{"axis": "spec", "severity": "note", "explanation": " "}],
        ["text"],
    ):
        with pytest.raises(ValueError):
            findings.parse(bad)
    returned, kept = findings.split(
        [finding("blocker"), finding("should_fix"), finding("note")], should_fix_returns=False
    )
    assert [f["severity"] for f in returned] == ["blocker"]
    assert [f["severity"] for f in kept] == ["should_fix", "note"]


def test_a_malformed_findings_list_gets_a_fresh_review(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    calls = wire(
        home, lambda n: [{"axis": "spec", "severity": "huge", "explanation": "x"}] if n == 0 else []
    )
    run = start(home, folder(tmp_path))
    assert run["state"] == "succeeded", run
    # The blocker can't be lost to a typo: the review runs again instead of passing.
    assert len(calls["review"]) == 2 and len(calls["work"]) == 1
