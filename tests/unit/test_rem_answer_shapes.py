"""Every 'Return ONLY JSON {...}' shape in Rem's prompts names fields Raticode reads.

A shape is the line that tells the model what to answer with. A key it lists that no model
reads is lost or refused; a shape that says 'no other fields' and leaves out a field the
rest of the same rules ask for makes the model choose between two instructions.
"""

from __future__ import annotations

import re
from collections.abc import Set as AbstractSet
from pathlib import Path

import pytest

import gofer.ui.rem_home as rem_home_module
from gofer.ui.rem_home import (
    CONVERSATION_RULES,
    REPLY_RULES,
    REVIEW_DECISIONS,
    REVIEW_RULES,
    TASK_UPDATE_RULES,
    WORKER_ANSWER_RULES,
)
from gofer.ui.rem_home_models import ConversationDecision, Decision, TaskUpdate, WorkerReply

SHAPE = re.compile(r"Return\s+ONLY\s+JSON\s*\{")
# 'return decision_edits:[...]', 'return tickets entries {...}': a top-level field the
# rules ask the model to fill. Lowercase only, so 'Return ONLY' isn't read as a field.
ASKED = re.compile(r"\breturn\s+([a-z_]+)(?::|\s+entries\b)")
VERDICT_READ = re.compile(r"verdict(?:\.get\(|\[)\"([a-z_]+)\"")


def model_fields(model: type) -> set[str]:
    """The top-level keys a model accepts, aliases included."""
    keys = set(model.model_fields)
    for field in model.model_fields.values():
        alias = field.validation_alias
        keys.update(getattr(alias, "choices", []) if alias is not None else [])
    return keys


def review_verdict_keys() -> set[str]:
    """The keys Raticode reads from a reviewer's verdict."""
    return set(VERDICT_READ.findall(Path(rem_home_module.__file__).read_text(encoding="utf-8")))


def shapes(rules: str) -> list[tuple[list[str], bool]]:
    """Each shape's top-level keys, and whether it says 'no other fields'."""
    found = []
    for match in SHAPE.finditer(rules):
        depth, keys, current = 1, [], ""
        index = match.end()
        while True:
            char = rules[index]
            index += 1
            if char in "{[":
                depth += 1
            elif char in "}]":
                depth -= 1
                if not depth:
                    break
            if depth == 1 and char == ",":
                keys.append(current)
                current = ""
            else:
                current += char
        keys.append(current)
        names = [key.split(":", 1)[0].strip() for key in keys]
        closed = rules[index : index + 30].lstrip().startswith("and no other fields")
        found.append((names, closed))
    return found


def shape_problems(
    rules: str, read: set[str], complete: AbstractSet[str] = frozenset()
) -> list[str]:
    """What is wrong with the shapes in rules, against the keys the reading model takes.

    complete is what a shape that says 'no other fields' must list: every field the model
    accepts, less the ones added only under certain conditions.
    """
    found = shapes(rules)
    if not found:
        return ["no 'Return ONLY JSON {...}' shape"]
    problems = []
    asked = set(ASKED.findall(rules))
    for names, closed in found:
        problems += [f"{name} is not read" for name in names if name not in read]
        if closed:
            problems += [f"{name} is asked for but missing" for name in sorted(asked - set(names))]
            problems += [
                f"{name} is accepted but missing" for name in sorted(complete - asked - set(names))
            ]
    problems += [f"{name} is asked for but not read" for name in sorted(asked - read)]
    return problems


# Fields Decision accepts that the chat shape leaves out because only some turns add them:
# reply_turn_rules adds phone_actions:[] for a message from the phone.
CHAT_CONDITIONAL = {"phone_actions"}


def complete(model: type, conditional: AbstractSet[str] = frozenset()) -> set[str]:
    """The fields a closed shape for model must list."""
    return set(model.model_fields) - conditional


CASES = {
    "chat reply": (REPLY_RULES, model_fields(Decision), complete(Decision, CHAT_CONDITIONAL)),
    "task update": (TASK_UPDATE_RULES, model_fields(TaskUpdate), complete(TaskUpdate)),
    "worker answer": (WORKER_ANSWER_RULES, model_fields(WorkerReply), complete(WorkerReply)),
    "conversation": (
        CONVERSATION_RULES,
        model_fields(ConversationDecision),
        complete(ConversationDecision),
    ),
    "review": (REVIEW_RULES + REVIEW_DECISIONS, review_verdict_keys(), review_verdict_keys()),
}


@pytest.mark.parametrize("name", CASES)
def test_every_shape_names_only_fields_its_model_reads(name: str):
    rules, read, closed_fields = CASES[name]
    assert shape_problems(rules, read, closed_fields) == []


def test_conditional_chat_fields_are_real_and_added_by_their_turn():
    assert CHAT_CONDITIONAL <= set(Decision.model_fields)
    phone = rem_home_module.reply_turn_rules(
        {},
        from_phone=True,
        from_workflow=False,
        workflow_acts=False,
        outside_input=False,
        auto_on=False,
    )
    for name in CHAT_CONDITIONAL:
        assert name + ":[]" in phone and name not in REPLY_RULES


def test_the_chat_shape_lists_the_decision_fields_and_closes():
    names, closed = shapes(REPLY_RULES)[0]
    assert closed
    assert {"decision_requests", "decision_answers", "decision_edits"} <= set(names)
    assert {"org_approvals", "tickets", "memory_changes", "self_work"} <= set(names)


def test_review_reads_the_keys_the_reviewer_is_told_to_return():
    assert {"criteria", "findings", "feedback", "question_to_rem"} <= review_verdict_keys()


def without(rules: str, field: str) -> str:
    """rules with one top-level field taken out of its first shape."""
    start = SHAPE.search(rules)
    assert start is not None
    end = rules.index("} and no other fields", start.end())
    shape = rules[start.end() - 1 : end + 1]
    trimmed = re.sub(r"([{,])" + re.escape(field) + r"(,|})", _rejoin, shape, count=1)
    assert trimmed != shape
    return rules[: start.end() - 1] + trimmed + rules[end + 1 :]


def _rejoin(match: re.Match[str]) -> str:
    """Keep one separator where a field was cut out."""
    before, after = match.groups()
    return before if after == "," else after


@pytest.mark.parametrize(
    "field", ["decision_edits:[]", "decision_requests:[]", "org_approvals:[]", "tickets:[]"]
)
def test_an_asked_field_dropped_from_the_closed_chat_shape_is_caught(field: str):
    name = field.split(":")[0]
    problems = shape_problems(
        without(REPLY_RULES, field), model_fields(Decision), CASES["chat reply"][2]
    )
    assert problems == [f"{name} is asked for but missing"]


@pytest.mark.parametrize("field", ["reply", "memories:[]", "tasks:[]", "files:[]"])
def test_any_accepted_field_dropped_from_the_closed_chat_shape_is_caught(field: str):
    name = field.split(":")[0]
    problems = shape_problems(
        without(REPLY_RULES, field), model_fields(Decision), CASES["chat reply"][2]
    )
    assert problems == [f"{name} is accepted but missing"]


def test_a_key_no_model_reads_is_caught():
    unread = TASK_UPDATE_RULES.replace("needs_user:false,", "needs_user:false,mood:happy,")
    assert shape_problems(unread, model_fields(TaskUpdate)) == ["mood is not read"]
