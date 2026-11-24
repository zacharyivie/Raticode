"""Approval gates with option buttons and a typed reply."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
import pytest
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.core.approvals import ApprovalRequest, ApprovalStore
from gofer.rattish.diagnostics import RattishCompileError
from gofer.rattish.runtime import execute_node
from gofer.ui.api import WorkflowApprovalError, decide_workflow_approval_payload
from tests.unit.test_rattish_remaining_nodes import compile_source

CHOICE_GATE = """Rattish: 1
Workflow:
  name: Channel
Node channel:
  type: approval-gate
  message: Where should this build go?
  options:
    - Stable
    - Beta
    - Cancel
  reject-options:
    - Cancel
  reply: {reply}
{extra}"""


def _gate(reply: str = "optional", extra: str = "") -> str:
    return CHOICE_GATE.format(reply=reply, extra=extra)


def _choice_request(**changes: Any) -> ApprovalRequest:
    values: dict[str, Any] = {
        "workflow_id": "deploy",
        "run_id": "run1",
        "node_id": "channel",
        "message": "Where?",
        "options": ["Stable", "Beta", "Cancel"],
        "reject_options": ["Cancel"],
        "reply_mode": "optional",
    }
    values.update(changes)
    return ApprovalRequest(**values)


async def _run_gate(
    tmp_path: Path, source: str, answer: Any
) -> tuple[dict[str, Any], ApprovalStore]:
    ir = compile_source(source, tmp_path)
    store = ApprovalStore(tmp_path / "data")
    holder: dict[str, Any] = {}

    async def run() -> None:
        holder["result"] = await execute_node(
            ir, "channel", data_dir=tmp_path / "data", approval_store=store, run_id="run1"
        )

    async with anyio.create_task_group() as group:
        group.start_soon(run)
        if answer is not None:
            while not store.list_pending():
                await anyio.sleep(0.01)
            answer(store)
    result = holder["result"]
    assert result.outcome == "success"
    return result.output, store


@pytest.mark.anyio
async def test_choice_sets_output_and_reject_options_set_approved(tmp_path: Path) -> None:
    output, store = await _run_gate(
        tmp_path,
        _gate(),
        lambda s: s.decide(
            "remaining-nodes", "run1", "channel", choice="cancel", reply=" Too risky "
        ),
    )
    assert output["choice"] == "Cancel"
    assert output["reply"] == "Too risky"
    assert output["decision"] == "rejected" and output["approved"] is False
    stored = store.get("remaining-nodes", "run1", "channel")
    assert stored is not None and stored.options == ["Stable", "Beta", "Cancel"]
    assert stored.decision is not None and stored.decision.choice == "Cancel"


@pytest.mark.anyio
async def test_other_choices_count_as_approved(tmp_path: Path) -> None:
    output, _ = await _run_gate(
        tmp_path,
        _gate(reply="none"),
        lambda s: s.decide("remaining-nodes", "run1", "channel", choice="Beta"),
    )
    assert output["choice"] == "Beta" and output["reply"] == ""
    assert output["decision"] == "approved" and output["approved"] is True


@pytest.mark.anyio
async def test_plain_gate_output_has_null_choice(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Plain
Node channel:
  type: approval-gate
  message: Ship it?
"""
    output, _ = await _run_gate(
        tmp_path,
        source,
        lambda s: s.decide("remaining-nodes", "run1", "channel", "approved"),
    )
    assert output["choice"] is None and output["reply"] == ""
    assert output["approved"] is True


@pytest.mark.anyio
async def test_timeout_picks_no_option(tmp_path: Path) -> None:
    source = _gate(reply="required").replace(
        "  reply: required", "  reply: required\n  decision-timeout: 50ms"
    )
    output, _ = await _run_gate(tmp_path, source, None)
    assert output["decision"] == "timeout" and output["approved"] is False
    assert output["choice"] is None and output["reply"] == ""


@pytest.mark.parametrize(
    ("request_changes", "answer", "message"),
    [
        ({}, {"decision": "approved"}, "choose one of: Stable, Beta, Cancel"),
        ({}, {"choice": "Gamma"}, "isn't one of this approval's options"),
        ({"reply_mode": "required"}, {"choice": "Beta", "reply": "  "}, "needs a reply"),
        ({"reply_mode": "none"}, {"choice": "Beta", "reply": "hi"}, "doesn't take a reply"),
        ({"options": [], "reject_options": []}, {"choice": "Beta"}, "has no options"),
    ],
)
def test_store_refuses_answers_the_gate_does_not_take(
    tmp_path: Path, request_changes: dict[str, Any], answer: dict[str, Any], message: str
) -> None:
    store = ApprovalStore(tmp_path)
    store.create_or_update(_choice_request(**request_changes))
    with pytest.raises(ValueError, match=message):
        store.decide("deploy", "run1", "channel", **answer)
    request = store.get("deploy", "run1", "channel")
    assert request is not None and request.status == "pending"


def test_request_round_trips_options_and_reply(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path)
    store.create_or_update(_choice_request())
    decided = store.decide("deploy", "run1", "channel", choice="BETA", reply="ok")
    again = ApprovalRequest.from_dict(decided.to_dict())
    assert again.options == ["Stable", "Beta", "Cancel"]
    assert again.reject_options == ["Cancel"] and again.reply_mode == "optional"
    assert again.decision is not None
    assert (again.decision.choice, again.decision.reply) == ("Beta", "ok")
    # Requests written before options existed still load as plain gates.
    legacy = ApprovalRequest.from_dict({"workflowId": "w", "runId": "r", "nodeId": "n"})
    assert legacy.options == [] and legacy.reply_mode == "none"


def _codes(source: str, tmp_path: Path) -> list[str]:
    with pytest.raises(RattishCompileError) as caught:
        compile_source(source, tmp_path)
    return [item.code for item in caught.value.diagnostics if item.severity == "error"]


ROUTED = """
  to:
    - ship when node.channel.output.choice == {label}
Node ship:
  type: bash-command
  command: echo ship
  needs: channel
"""


def test_routes_on_choice_must_name_a_listed_option(tmp_path: Path) -> None:
    ir = compile_source(_gate(extra=ROUTED.format(label='"Stable"')), tmp_path)
    gate = next(node for node in ir["nodes"] if node["id"] == "channel")
    assert gate["output"]["schema"]["properties"]["choice"]["enum"] == [
        "Stable",
        "Beta",
        "Cancel",
        None,
    ]
    assert _codes(_gate(extra=ROUTED.format(label='"stable"')), tmp_path) == [
        "RATTISH_APPROVAL_CHOICE_UNKNOWN"
    ]
    reversed_source = _gate(extra=ROUTED.format(label='"Gamma"')).replace(
        'node.channel.output.choice == "Gamma"', '"Gamma" != node.channel.output.choice'
    )
    assert _codes(reversed_source, tmp_path) == ["RATTISH_APPROVAL_CHOICE_UNKNOWN"]


def test_route_on_choice_of_gate_without_options_is_rejected(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Plain
Node channel:
  type: approval-gate
  message: Ship it?
""" + ROUTED.format(label='"Stable"')
    assert _codes(source, tmp_path) == ["RATTISH_APPROVAL_CHOICE_UNKNOWN"]


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (("    - Cancel\n  reply", "    - Never\n  reply"), "RATTISH_INVALID_FIELD_VALUE"),
        (("    - Beta\n", "    - stable\n"), "RATTISH_INVALID_FIELD_VALUE"),
        (("    - Beta\n    - Cancel\n  reject", "  reject"), "RATTISH_INVALID_FIELD_VALUE"),
        (("  reply: optional", "  reply: sometimes"), "RATTISH_INVALID_FIELD_VALUE"),
    ],
    ids=["reject-option-not-listed", "duplicate-ignoring-case", "one-option", "bad-reply"],
)
def test_invalid_option_configuration(tmp_path: Path, change: tuple[str, str], code: str) -> None:
    source = _gate().replace(*change)
    assert source != _gate()
    assert code in _codes(source, tmp_path)


def test_api_answers_with_a_choice_and_refuses_plain_reject(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path)
    store.create_or_update(_choice_request())
    with pytest.raises(WorkflowApprovalError, match="options instead of Reject"):
        decide_workflow_approval_payload("deploy", "run1", "channel", "rejected", tmp_path)
    with pytest.raises(WorkflowApprovalError, match="choose one of"):
        decide_workflow_approval_payload("deploy", "run1", "channel", "approved", tmp_path)
    payload = decide_workflow_approval_payload(
        "deploy", "run1", "channel", "approved", tmp_path, choice="Cancel", reply="No"
    )
    approval = payload["approval"]
    assert approval["options"] == ["Stable", "Beta", "Cancel"]
    assert approval["decision"]["decision"] == "rejected"
    assert (approval["decision"]["choice"], approval["decision"]["reply"]) == ("Cancel", "No")


def test_legacy_cli_answers_with_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import gofer.cli.commands.workflow as workflow_commands

    monkeypatch.setattr(workflow_commands, "in_agent_session", lambda: False)
    store = ApprovalStore(tmp_path)
    store.create_or_update(_choice_request(workflow_path=str(tmp_path / "workflow.rattish")))
    runner = CliRunner()
    base = ["workflow", "approve", "run1", "channel", "--data-dir", str(tmp_path)]
    missing = runner.invoke(app, base)
    assert missing.exit_code == 1 and "choose one of" in missing.output
    refused = runner.invoke(
        app, ["workflow", "reject", "run1", "channel", "--data-dir", str(tmp_path)]
    )
    assert refused.exit_code == 1 and "approve --choice" in refused.output
    answered = runner.invoke(app, [*base, "--choice", "beta", "--reply", "Testers only"])
    assert answered.exit_code == 0, answered.output
    assert "Answered Beta" in answered.output
    decided = store.get("deploy", "run1", "channel")
    assert decided is not None and decided.decision is not None
    assert (decided.decision.choice, decided.decision.reply) == ("Beta", "Testers only")
    assert decided.decision.decision == "approved"
