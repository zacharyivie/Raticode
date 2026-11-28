"""Tokens and cost per Rattish run and node, and the run's max-tokens and max-cost-usd."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gofer.core.agent import AgentResult
from gofer.core.provider_profiles import ResolvedProviderSettings
from gofer.core.usage_context import ProviderCall
from gofer.core.usage_ledger import usage_db
from gofer.rattish.run_service import resume_refusal, run_rattish_file
from gofer.rattish.run_usage import (
    RunUsageMeter,
    cost_label,
    empty_usage,
    tokens_label,
    usage_line,
)
from gofer.subscriptions.base import Subscription
from gofer.subscriptions.usage import track_invocation
from gofer.utils.paths import get_data_dir


class MeteredFake(Subscription):
    """A provider whose calls go through the real usage-ledger boundary."""

    provider = "codex"

    def __init__(self, usages: list[dict[str, Any]], *, outputs: list[str] | None = None) -> None:
        self.usages = list(usages)
        self.outputs = list(outputs or [])
        self.calls = 0

    def _build_command(self, *args: Any, **kwargs: Any) -> list[str]:
        return ["fake"]

    def is_available(self) -> bool:
        return True

    @track_invocation
    async def execute(
        self,
        prompt: str,
        working_dir: Path,
        tools: list[str],
        mcp_servers: list[str],
        env: dict[str, str],
        timeout: float | None = None,
        cancel_event: Any | None = None,
        extra_paths: list[Path] | None = None,
        max_output_bytes: int | None = None,
        on_thought: Callable[[str], None] | None = None,
        provider_settings: ResolvedProviderSettings | None = None,
    ) -> AgentResult:
        self.calls += 1
        usage = self.usages.pop(0) if self.usages else {}
        output = self.outputs.pop(0) if self.outputs else "done"
        return AgentResult(
            agent_id="",
            success=True,
            output=output,
            exit_code=0,
            duration_seconds=0,
            usage_metadata=usage,
        )


def _source(tmp_path: Path, workflow_fields: str = "", *, nodes: int = 2) -> Path:
    body = [f"Rattish: 1\n\nWorkflow:\n  name: Usage\n{workflow_fields}"]
    for index in range(1, nodes + 1):
        body.append(
            f"\nNode step-{index}:\n  type: agent\n  provider: codex\n  prompt: Step {index}\n"
            + (f"  to: step-{index + 1}\n" if index < nodes else "")
            + (f"  needs: step-{index - 1}\n" if index > 1 else "")
        )
    source = tmp_path / "project" / "workflow.rattish"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("".join(body), encoding="utf-8")
    return source


async def _run(source: Path, provider: MeteredFake, tmp_path: Path) -> dict[str, Any]:
    result = await run_rattish_file(
        source, data_dir=tmp_path / "data", subscriptions={"codex": provider}
    )
    saved = json.loads(result.path.read_text(encoding="utf-8"))
    assert saved == json.loads(json.dumps(result.document))
    return result.document


def _ledger_rows() -> list[dict[str, Any]]:
    with usage_db(get_data_dir()) as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT provider, run_id, node_id, tokens, cost_usd FROM usage_invocations "
                "ORDER BY observed_at"
            )
        ]


@pytest.mark.anyio
async def test_run_and_node_usage_are_saved_and_ledger_calls_name_the_run(
    tmp_path: Path,
) -> None:
    provider = MeteredFake(
        [
            {"input_tokens": 100, "output_tokens": 20, "total_cost_usd": 0.01},
            {"input_tokens": 300, "output_tokens": 30, "total_cost_usd": 0.02},
        ]
    )

    document = await _run(_source(tmp_path), provider, tmp_path)

    assert document["status"] == "passed"
    usage = document["usage"]
    assert usage["calls"] == 2
    assert usage["total_tokens"] == 450
    assert usage["cost_usd"] == pytest.approx(0.03)
    assert usage["limits"] == {"max_tokens": None, "max_cost_usd": None}
    assert usage["nodes"]["step-1"]["total_tokens"] == 120
    assert usage["nodes"]["step-2"]["cost_usd"] == pytest.approx(0.02)
    assert [record["usage"]["total_tokens"] for record in document["runs"]] == [120, 330]
    rows = _ledger_rows()
    assert [(row["run_id"], row["node_id"]) for row in rows] == [
        (document["run_id"], "step-1"),
        (document["run_id"], "step-2"),
    ]
    assert [row["cost_usd"] for row in rows] == pytest.approx([0.01, 0.02])


@pytest.mark.anyio
async def test_unreported_cost_and_tokens_read_as_unknown_not_zero(tmp_path: Path) -> None:
    provider = MeteredFake([{}, {"input_tokens": 10, "output_tokens": 5}])

    document = await _run(_source(tmp_path), provider, tmp_path)

    usage = document["usage"]
    assert usage["cost_usd"] is None
    assert usage["cost_unknown_calls"] == 2
    assert usage["nodes"]["step-1"]["total_tokens"] is None
    assert tokens_label(usage["nodes"]["step-1"]) == "unknown"
    assert cost_label(usage) == "unknown"
    assert tokens_label(usage) == "at least 15 (1 of 2 calls unreported)"
    assert _ledger_rows()[0]["cost_usd"] is None


@pytest.mark.anyio
async def test_max_tokens_ends_the_run_with_a_budget_error_after_the_crossing_call(
    tmp_path: Path,
) -> None:
    provider = MeteredFake(
        [{"input_tokens": 400, "output_tokens": 200}, {"input_tokens": 1, "output_tokens": 1}]
    )
    source = _source(tmp_path, "  max-tokens: 500\n", nodes=3)

    document = await _run(source, provider, tmp_path)

    assert provider.calls == 1
    assert document["status"] == "failed"
    error = document["error"]
    assert error["kind"] == "budget"
    assert error["code"] == "RATTISH_TOKEN_LIMIT_EXCEEDED"
    assert "600 tokens" in error["message"] and "max-tokens limit of 500" in error["message"]
    assert "already running" in error["message"]
    assert error["details"]["node_id"] == "step-1"
    assert document["usage"]["limits"]["max_tokens"] == 500
    assert "max-tokens" in str(resume_refusal(document, None))


@pytest.mark.anyio
async def test_max_cost_usd_ends_the_run_once_the_reported_cost_is_over(tmp_path: Path) -> None:
    provider = MeteredFake(
        [
            {"input_tokens": 1, "output_tokens": 1, "total_cost_usd": 0.3},
            {"input_tokens": 1, "output_tokens": 1, "total_cost_usd": 0.3},
            {"input_tokens": 1, "output_tokens": 1, "total_cost_usd": 0.3},
        ]
    )
    source = _source(tmp_path, "  max-cost-usd: 0.5\n", nodes=3)

    document = await _run(source, provider, tmp_path)

    assert provider.calls == 2
    assert document["error"]["kind"] == "budget"
    assert document["error"]["code"] == "RATTISH_COST_LIMIT_EXCEEDED"
    assert "$0.60" in document["error"]["message"]
    assert "$0.50" in document["error"]["message"]
    assert document["usage"]["cost_usd"] == pytest.approx(0.6)


@pytest.mark.anyio
async def test_max_cost_usd_with_a_provider_that_reports_no_cost_ends_the_run(
    tmp_path: Path,
) -> None:
    provider = MeteredFake([{"input_tokens": 1, "output_tokens": 1}] * 2)
    source = _source(tmp_path, "  max-cost-usd: 5\n")

    document = await _run(source, provider, tmp_path)

    assert provider.calls == 1
    assert document["error"]["kind"] == "budget"
    assert document["error"]["code"] == "RATTISH_COST_LIMIT_UNCHECKABLE"
    assert "didn't report what it cost" in document["error"]["message"]
    assert document["usage"]["cost_usd"] is None


@pytest.mark.anyio
async def test_runs_under_their_limits_pass(tmp_path: Path) -> None:
    provider = MeteredFake([{"input_tokens": 10, "output_tokens": 10, "total_cost_usd": 0.001}] * 2)
    source = _source(tmp_path, "  max-tokens: 40\n  max-cost-usd: 0.01\n")

    document = await _run(source, provider, tmp_path)

    assert document["status"] == "passed"
    assert document["usage"]["total_tokens"] == 40


@pytest.mark.anyio
async def test_a_crossed_limit_stops_structured_output_repairs(tmp_path: Path) -> None:
    source = tmp_path / "project" / "workflow.rattish"
    source.parent.mkdir(parents=True)
    source.write_text(
        """Rattish: 1

Workflow:
  name: Repairs
  max-tokens: 10

Node review:
  type: agent
  provider: codex
  prompt: Review
  repair-attempts: 3
  output-schema: {"type": "object"}
""",
        encoding="utf-8",
    )
    provider = MeteredFake(
        [{"input_tokens": 50, "output_tokens": 50}] * 4, outputs=["not json"] * 4
    )

    document = await _run(source, provider, tmp_path)

    assert provider.calls == 1
    assert document["error"]["code"] == "RATTISH_TOKEN_LIMIT_EXCEEDED"


def test_meter_forwards_child_workflow_calls_to_the_calling_node() -> None:
    parent = RunUsageMeter("parent-run", max_tokens=100)
    child = RunUsageMeter("child-run", parent=parent.observer("call-child", 1))
    observer = child.observer("inner", 1)
    call = ProviderCall("codex", None, {"total_tokens": 150}, None, False, False)

    assert (observer.run_id, observer.node_id) == ("parent-run", "call-child")
    observer.observe(call)

    assert parent.snapshot()["nodes"]["call-child"]["total_tokens"] == 150
    assert child.snapshot()["nodes"]["inner"]["total_tokens"] == 150
    assert child.stop_requested()
    assert child.limit_reached() is not None
    assert parent.limit_reached()["code"] == "RATTISH_TOKEN_LIMIT_EXCEEDED"  # type: ignore[index]


@pytest.mark.parametrize(
    ("tokens", "partial", "estimated", "bucket", "total"),
    [
        ({"total_tokens": 9}, False, False, "reported_calls", 9),
        ({"input_tokens": 4, "output_tokens": 5}, False, False, "reported_calls", 9),
        ({"input_tokens": 4}, False, False, "partial_calls", 4),
        ({"input_tokens": 4, "output_tokens": 5}, True, False, "partial_calls", 9),
        ({}, False, False, "unknown_calls", None),
        ({"total_tokens": 9}, False, True, "unknown_calls", None),
    ],
)
def test_run_and_ticket_totals_count_a_call_the_same_way(
    tokens: dict[str, int], partial: bool, estimated: bool, bucket: str, total: int | None
) -> None:
    from gofer.core import usage_ledger
    from gofer.rattish.run_usage import add_call

    run = empty_usage()
    add_call(run, ProviderCall("codex", None, tokens, None, partial, estimated))
    ticket = usage_ledger.empty_usage()
    usage_ledger.count_tokens(ticket, {} if estimated else tokens, partial)
    assert ticket == {field: run[field] for field in ticket}
    assert run[bucket] == 1
    assert run["total_tokens"] == total


def test_labels_never_turn_unknown_into_zero() -> None:
    usage = empty_usage()
    assert usage_line(usage) == "No model calls"
    usage.update(calls=1, unknown_calls=1, cost_unknown_calls=1)
    assert tokens_label(usage) == "unknown"
    assert cost_label(usage) == "unknown"
    assert usage_line(usage) == "1 model call, tokens unknown, estimated cost unknown"
    usage.update(cost_usd=0.004, cost_reported_calls=1, cost_unknown_calls=0)
    assert cost_label(usage) == "$0.0040"


def test_cli_text_shows_run_and_node_usage() -> None:
    from gofer.cli.commands.workflows import _usage_cells, _usage_text
    from gofer.rattish.run_usage import usage_payload

    usage = {
        **empty_usage(),
        "calls": 2,
        "reported_calls": 1,
        "unknown_calls": 1,
        "total_tokens": 120,
        "cost_unknown_calls": 2,
        "nodes": {
            "draft": {
                **empty_usage(),
                "calls": 1,
                "reported_calls": 1,
                "total_tokens": 120,
                "cost_unknown_calls": 1,
            },
            "review": {**empty_usage(), "calls": 1, "unknown_calls": 1, "cost_unknown_calls": 1},
        },
        "limits": {"max_tokens": 1000, "max_cost_usd": None},
    }
    payload = usage_payload(usage)
    assert payload is not None

    assert _usage_cells(payload) == ["at least 120 (1 of 2 calls unreported)", "unknown"]
    assert _usage_cells(None) == ["-", "-"]
    assert _usage_text(payload).splitlines() == [
        "Usage: 2 model calls, tokens at least 120 (1 of 2 calls unreported), "
        "estimated cost unknown",
        "Limits: max-tokens 1000",
        "  draft: tokens 120, estimated cost unknown (1 calls)",
        "  review: tokens unknown, estimated cost unknown (1 calls)",
    ]


def test_rattish_run_text_reports_usage_when_models_were_called(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from types import SimpleNamespace

    from gofer.cli.commands.rattish import _emit_run

    document: dict[str, Any] = {
        "source": {"path": "workflow.rattish"},
        "run_id": "run-1",
        "diagnostics": [],
        "error": None,
        "outputs": {},
        "usage": {**empty_usage(), "calls": 1, "unknown_calls": 1, "cost_unknown_calls": 1},
    }
    result = SimpleNamespace(
        document=document, path=tmp_path / "run.json", ok=True, status="passed"
    )

    _emit_run(result, "text")  # type: ignore[arg-type]

    assert "Usage: 1 model call, tokens unknown, estimated cost unknown" in capsys.readouterr().out
