"""The worker and review prompts read the same after moving out of RemHome.

tests/fixtures/rem_run_prompts/golden.json was captured on dev 2e5c98ea, before the
move, by running RemHome._run's and RemHome._turn's own lines on fixed runs. Each case
holds its inputs and the text dev built from them. Text that other modules own, such as
REVIEW_RULES or auto mode's guidance, is stored as ``{{NAME}}`` and filled in from the
current constant, so rewording one of those doesn't break this test; the words, order
and joins these functions add are compared exactly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import rem_auto_mode as auto_mode
from gofer.ui import rem_home
from gofer.ui import rem_project_checks as project_checks
from gofer.ui import rem_review_findings as review_findings
from gofer.ui import rem_run_prompts as run_prompts
from gofer.ui import rem_task_after as after_tasks
from gofer.ui.rem_home_models import OCCURRENCE_GUIDANCE

GOLDEN = Path(__file__).parents[1] / "fixtures" / "rem_run_prompts" / "golden.json"
CASES: list[dict[str, Any]] = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]

SHARED = {
    "REVIEW_RULES": run_prompts.REVIEW_RULES,
    "REVIEW_DECISIONS": run_prompts.REVIEW_DECISIONS,
    "REVIEW_PROVING_TEST": run_prompts.REVIEW_PROVING_TEST,
    "PROVING_TEST_GUIDANCE": run_prompts.PROVING_TEST_GUIDANCE,
    "EVIDENCE_LINKS": run_prompts.EVIDENCE_LINKS,
    "OCCURRENCE_GUIDANCE": OCCURRENCE_GUIDANCE,
    "auto_mode.WORKER_GUIDANCE": auto_mode.WORKER_GUIDANCE,
    "auto_mode.REVIEW_GUIDANCE": auto_mode.REVIEW_GUIDANCE,
    "review_findings.REVIEW_FORMAT": review_findings.REVIEW_FORMAT,
    "project_checks.REVIEW_GUIDANCE": project_checks.REVIEW_GUIDANCE,
    "after_tasks.PREREQUISITE_GUIDANCE": after_tasks.PREREQUISITE_GUIDANCE,
}


def expected(case: dict[str, Any]) -> str:
    text = str(case["expected"])
    for name, value in SHARED.items():
        text = text.replace("{{" + name + "}}", value)
    assert "{{" not in text, "golden names a constant this test doesn't fill in"
    return text


def built(case: dict[str, Any]) -> str:
    run = case["run"]
    if case["kind"] == "worker_instructions":
        return run_prompts.worker_instructions(
            run,
            glossary=case["glossary"],
            auto=case["auto"],
            sleeping_guidance=case["sleeping_guidance"],
        )
    if case["kind"] == "review_prompt":
        return run_prompts.review_prompt(
            run,
            decided=case["decided"],
            check=case["check"],
            proving=case["proving"],
            answer=case["answer"],
            traces=case["traces"],
            artifacts=case["artifacts"],
            before=case["before"],
            auto=case["auto"],
            standards=case["standards"],
        )
    # _turn's worktree, skill and check text, in the order _turn adds them.
    assert case["kind"] == "turn_text"
    text = run_prompts.worktree_text(run, Path(case["root"]), run["recipe"])
    text += run_prompts.skill_text(case["skill"], run["recipe"], case["landing"])
    return text + run_prompts.check_text(case["check"])


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_prompt_matches_the_text_dev_built_before_the_move(case: dict[str, Any]) -> None:
    assert built(case) == expected(case)


def test_golden_covers_every_moved_prompt() -> None:
    names = {c["name"] for c in CASES}
    assert {
        "plain",
        "auto_mode",
        "proving_test",
        "research_with_rem_artifacts",
        "research_without_rem_artifacts",
        "review_plain",
        "review_decided_check",
        "review_recovery_inspection",
        "worktree_setup_passed",
        "worktree_setup_failed",
        "skill_with_landing",
        "decided_check",
    } <= names
    # Every shared constant shows up in at least one golden string.
    joined = "".join(c["expected"] for c in CASES)
    assert all("{{" + name + "}}" in joined for name in SHARED)


def test_rem_home_still_exports_the_moved_constants() -> None:
    """Tests and callers import these from rem_home; they are the same objects."""
    for name in (
        "EVIDENCE_LINKS",
        "REVIEW_RULES",
        "REVIEW_DECISIONS",
        "PROVING_TEST_GUIDANCE",
        "REVIEW_PROVING_TEST",
        "proving_test_asked",
        "needs_saved_report",
    ):
        assert getattr(rem_home, name) is getattr(run_prompts, name)


def test_no_worktree_skill_or_check_adds_no_text() -> None:
    run = {"recipe": {"project_dir": "/p"}}
    assert run_prompts.worktree_text(run, Path("/p"), run["recipe"]) == ""
    assert run_prompts.skill_text(None, run["recipe"], "dev") == ""
    assert run_prompts.check_text(None) == ""
