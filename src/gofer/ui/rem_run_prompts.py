"""The words of a Rem task's worker and review turns, built from what RemHome looked up.

RemHome reads the store, the project's decisions, its glossary and its check; the
functions here only put those results into the text a worker or a reviewer gets, so
each prompt can be tested without a running Rem (tests/unit/test_rem_run_prompts.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gofer.subscriptions.tool_trace import per_call_receipts
from gofer.ui import rem_auto_mode as auto_mode
from gofer.ui import rem_project_checks as project_checks
from gofer.ui import rem_proving_test as proving_tests
from gofer.ui import rem_review_findings as review_findings
from gofer.ui import rem_task_after as after_tasks
from gofer.ui.rem_home_models import OCCURRENCE_GUIDANCE, occurrence_context, read_only_recipe
from gofer.ui.rem_home_store import encode

# Every final message to the user links the evidence files behind it; the chat shows each
# linked file as an attachment under the message (file_mentions.linked_files).
EVIDENCE_LINKS = (
    "Link every evidence file the work produced for the user, such as screenshots, screen "
    "recordings, reports and logs, in the final message to the user as a Markdown link to "
    "its absolute path, [name](</absolute/path>), with the path copied exactly. Raticode "
    "shows each linked file as an attachment under the message."
)

# The fixed rules of an independent review; _run adds this run's guidance and records.
REVIEW_RULES = (
    "You are an independent read-only reviewer. Verify the original task against "
    "every acceptance criterion using actual artifacts and tests. Do not trust "
    "the worker's summary as proof. You may run non-mutating checks, but never "
    "edit source or save reports. Inspect the current files or run checks in "
    "this turn; findings remembered from an earlier review are not evidence. "
    "Check relevant security and regression issues. "
    "If evidence is missing return unknown, not pass. Return ONLY JSON "
    "{criteria:[{id,result:pass|fail|unknown,evidence:nonempty explanation}],"
    "findings:[...],feedback:short repair instructions,"
    "question_to_rem:question or empty string}. "
    "If the worker asked a clarification that prevents completing the task, "
    "put that question in question_to_rem. Rem is the coordinator, a separate "
    "agent who can answer routine questions and choose reasonable defaults. "
    "Do not assume a worker question needs the human's attention. A completed "
    "result with a closing pleasantry needs no clarification. "
    "For an audit or status request, identifying missing setup and explaining "
    "next steps can satisfy the task; do not require activating the audited "
    "software unless activation was requested. "
    "Every criterion ID must appear once. "
    "If a criterion can only be proven by an event that hasn't happened yet, "
    "such as the task's next scheduled run, mark it unknown and also return "
    "wait_until:ISO-8601 instant with timezone when that evidence should exist "
    "and wait_for:short description of the awaited event. Otherwise omit both. "
    "When occurrence is present, judge this run's period: covering what is new "
    "since new_since passes, and so does a clear report that nothing new happened. "
    "Earlier findings presented as new do not.\n"
)

# Added to a review when the change touches paths that decision records are about.
REVIEW_DECISIONS = (
    "The change touches paths that the decision records in decision_records are about. "
    "Also check the change against each of them and return decisions:[{id,result:pass|fail|"
    "unknown,evidence}] with every listed ID once. A change that breaks a record fails it, "
    "even when every criterion passes. If two decisions conflict so that no change can keep "
    "both, don't judge against a compromise: mark them unknown and return "
    "decision_conflicts:[{ids:[the IDs],why,fix:{question,answer,why}}] with one "
    "recommended clean replacement for them.\n"
)
# Develop and maintain tasks that write code name the test that proves them, and the
# reviewer looks for that test.
PROVING_TEST_GUIDANCE = (
    "\nName the test that proves this change. In your result, write `Proving test: "
    "<test name>` for a test that fails before the change and passes after it, or for an "
    "existing test that already covers it, and run that test so its name shows in your "
    "command output or in the project check's output. Write a pytest test as its node ID "
    "(`path/to/test_file.py::test_name`) or a Node test as its file for `node --test`: "
    "Raticode then runs it itself on the commit your work started from (with only your "
    "test files added) and on your commit, and shows the reviewer both results. Commit the "
    "test with the work. If no test can prove the change "
    "here, write `Not testable here because <reason>` instead, with the concrete reason, "
    "such as a real phone or an account this computer doesn't have. The reviewer checks "
    "either one."
)
REVIEW_PROVING_TEST = (
    "This task changes code, so the worker's result must name a proving test (`Proving "
    "test: ...`) or say `Not testable here because ...`. Check that the named test appears, "
    "run and passing, in raticode_check's output_tail or in a worker receipt. Judge a "
    "not-testable reason on its merits: accept a real limit of this computer or sandbox, "
    "such as a device, account or network it lacks, and reject one a test could cover. "
    "When the task's prompt asks to show the test or check failing before the fix and "
    "passing after it, look for both receipts: a run where it fails on the mistake and a "
    "run where it passes after the fix. "
    "When neither line is given, the named test isn't in either place, the reason doesn't "
    "hold, or an asked-for receipt is missing, fail the criterion closest to proving the "
    "change works and say what is missing in feedback. "
    "raticode_proving_test, when given, is Raticode's own run of the named test, which the "
    "worker can't write or change: in its sandbox it ran the test in a fresh worktree of the "
    "work's base commit (where the work left its landing branch) with only the test files "
    "the task added or changed, and in another of the work's commit. When its ran is true, "
    "trust that pair over the worker's own claim and receipts: failed_before and "
    "passes_after both true are the before and after receipts, even for a task that asks "
    "for them. passes_after false means the named test doesn't pass on the committed work: "
    "fail it. failed_before false means it didn't fail before the change, so it proves only "
    "that an existing test still passes: accept that only when the worker named an existing "
    "test that already covers the change and the task didn't ask for a failing-before "
    "receipt. When its status is 'not run by Raticode', not_run_reason says why; judge "
    "from raticode_check and the worker's receipts as above.\n"
)


def proving_test_asked(recipe: dict[str, Any]) -> bool:
    """Whether the worker was asked to name a proving test.

    Develop and maintain tasks that write code are; research, conversations, errands and
    reminders aren't.
    """
    return recipe.get("type") in {"develop", "maintain"} and not read_only_recipe(recipe)


def needs_saved_report(run: dict[str, Any]) -> bool:
    """Research saves a report, except self-work's backlog ranking, whose result is the ranking."""
    return bool(
        run["recipe"]["type"] == "research" and (run.get("self_work") or {}).get("kind") != "triage"
    )


def worker_instructions(
    run: dict[str, Any], *, glossary: str, auto: bool, sleeping_guidance: str
) -> str:
    """A worker turn's instructions: its role, limits, guidance and the private task.

    ``glossary`` is RemHome._glossary_pointer's line (or nothing), ``auto`` whether auto
    mode governs the run and ``sleeping_guidance`` rem_home_sleep.guidance for the turn.
    """
    instructions = (
        "You are a separate worker thread assigned by Rem, the user's coordinator. "
        "Rem can answer your clarifying questions and make routine decisions within "
        "the user-authorized goal. Direct questions to Rem rather than assuming the "
        "human user is your conversation partner. Perform this task within its "
        "authorized scope. "
        "Do not delegate the goal again. Inspect prior work before resuming. "
        "Report artifacts, actual check results, and blockers. "
        + EVIDENCE_LINKS
        + " Start your final "
        "answer with one short sentence describing the result for the user. "
        "Your answer does not accept the task. An independent reviewer will check it. "
        "Never deploy, publish, send external messages, spend money, or run a workflow "
        "unless the task explicitly authorizes that action. "
        "Rem can open and create local project folders. If this work needs a "
        "folder that isn't this task's project, such as a new project or a folder "
        "an organization should own, don't ask the user to open it: stop and name "
        "its absolute path in your result. Rem opens it and continues the work "
        "there. "
        + (
            # Only research turns are granted rem_artifacts, so
            # other tasks must not be told to wait for it.
            "Save the research report using rem_artifacts.save_report, which "
            "targets the selected Second Brain or reports/rem in the selected "
            "project. Give it a path relative to that folder. Saved reports are "
            "never replaced, so choose a new file name if one is taken. "
            "When the report cites Second Brain sources, pass its evidence, "
            "title and question as the tool's description explains. "
            "Research cannot change source code.\n"
            if needs_saved_report(run)
            else "This task has no rem_artifacts tool; save any report it asks "
            "for with the report or note tools available to you.\n"
        )
        # How to wait for CI and other outside jobs, and the sleep tool.
        + sleeping_guidance.lstrip("\n")
        + "\n"
        + (auto_mode.WORKER_GUIDANCE if auto else "")
        + (PROVING_TEST_GUIDANCE if proving_test_asked(run["recipe"]) else "")
        # One line naming the project's glossary, never its text.
        + (("\n" + glossary.rstrip("\n")) if glossary else "")
        + "\nPrivate task requirements:\n"
        + encode({key: run["recipe"][key] for key in ("type", "prompt", "acceptance_criteria")})
    )
    occurrence = occurrence_context(run)
    if occurrence:
        instructions += OCCURRENCE_GUIDANCE + encode(occurrence)
    if run.get("after_results"):
        instructions += after_tasks.PREREQUISITE_GUIDANCE + encode(run["after_results"])
    return instructions


def review_prompt(
    run: dict[str, Any],
    *,
    decided: dict[str, Any] | None,
    check: dict[str, Any] | None,
    proving: dict[str, Any] | None,
    answer: str,
    traces: list[dict[str, Any]],
    artifacts: list[Any],
    before: str,
    auto: bool,
    standards: str,
) -> str:
    """An independent review's instructions for one worker result.

    ``decided``, ``check`` and ``proving`` are RemHome's decision check, its run of the
    project's check and of the proving test (each None when it didn't run), ``before``
    the subject hash before the review and ``standards`` RemHome._standards_guidance.
    A restart recovery's saved worker conversation is added by RemHome.
    """
    prompt = (
        REVIEW_RULES
        + review_findings.REVIEW_FORMAT
        + standards
        + (auto_mode.REVIEW_GUIDANCE if auto else "")
        + (REVIEW_DECISIONS if decided and decided["records"] else "")
        + (project_checks.REVIEW_GUIDANCE if check is not None else "")
        + (REVIEW_PROVING_TEST if proving_test_asked(run["recipe"]) else "")
        + encode(
            {
                "recipe": run["recipe"],
                **(
                    {"decision_records": decided["records"]}
                    if decided and decided["records"]
                    else {}
                ),
                **({"raticode_check": project_checks.receipt(check)} if check is not None else {}),
                **(
                    {"raticode_proving_test": proving_tests.receipt(proving)}
                    if proving is not None
                    else {}
                ),
                "occurrence": occurrence_context(run),
                "worker_result": answer,
                "receipts": per_call_receipts(traces),
                "artifacts": artifacts,
                "subject_hash": before,
            }
        )
    )
    if run.get("recovery_inspection"):
        prompt += (
            "\nThis is restart recovery. Inspect the existing worker transcript, "
            "files and command receipts first. Do not repeat its actions. Also return "
            "safe_to_resume:true only if incomplete work can continue "
            "without repeating "
            "an uncertain external action, payment, deletion, deployment or message. "
            "Otherwise return false and explain precisely what cannot be established. "
            "Do not demand user input for facts found in Raticode records."
        )
    return prompt


def worktree_text(run: dict[str, Any], root: Path, recipe: dict[str, Any]) -> str:
    """What a worker in Rem's own worktree at ``root`` is told about it, or nothing."""
    if not run.get("worktree"):
        return ""
    text = (
        f"\nAnother Rem task is changing {recipe['project_dir']} at the same time, so "
        f"this task uses a temporary git worktree at {root} on branch "
        f"{run['worktree']['branch']}, created from the project's latest commit. Work "
        "only in this worktree, never in the original project folder, and commit "
        "finished changes to this branch. Install or link dependencies here if checks "
        "need them. Rem removes the worktree afterwards and keeps the branch."
    )
    setup = run.get("worktree_setup")
    if setup and setup.get("status") != "passed":
        text += (
            f"\n{setup.get('summary')} Prepare what your checks need yourself, and "
            "don't change the project's setup file to get around the approval."
        )
    elif setup:
        text += f"\nThe project's worktree setup already ran here: {setup['summary']}"
    return text


def skill_text(skill: str | None, recipe: dict[str, Any], landing: str | None) -> str:
    """How a worker follows the enabled worktree ``skill``, and lands on ``landing``.

    Nothing without a skill.
    """
    if not skill:
        return ""
    text = (
        f"\nThe enabled {skill} skill sets how changes to this project are made, and "
        "Rem has not created a worktree for this task. Unless the task says to work in "
        "place, follow the skill to choose or create the worktree and branch, and make "
        f"every edit, check and commit there. Other Rem tasks may be working in "
        f"{recipe['project_dir']} at the same time, so leave the checkout's own files "
        "and branch alone, with one exception: landing. A Rem task can't move its own "
        "thread to another folder: where the skill says to switch the thread into the "
        "worktree or to ask the user to, skip that step and keep working in the "
        "worktree by absolute path."
    )
    if landing:
        text += (
            "\nThe task isn't finished until its branch has landed on the project's "
            f"integration branch (local {landing}, the project's recorded decision): "
            f"rebase, rerun the checks and fast-forward {landing} as the skill "
            "describes. This task already authorizes that step, so don't wait for a "
            f"separate go-ahead. If {landing} is checked out in a folder with no "
            "uncommitted changes, including the project's own checkout, fast-forward "
            "it there with git merge --ff-only. If it can't land, for example because "
            "that folder has uncommitted changes or the rebase conflicts, say so in "
            "the first line of your result. Rem checks before accepting the task that "
            f"the branches it committed to are on {landing}."
        )
    return text


def check_text(check: dict[str, Any] | None) -> str:
    """The worker's notice that Raticode runs the project's decided ``check`` itself.

    Nothing without a check.
    """
    if not check:
        return ""
    return (
        f"\nBefore the review, Raticode runs this project's decided check "
        f"({check['id']}) itself in the task's worktree: `{check['answer']}`. It runs "
        "in a sandbox with no network that can write only to that folder and its own "
        "temporary folder. Run it yourself before you finish; a failure comes back "
        "to you with its output."
    )
