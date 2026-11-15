"""Workflow usage limits inside an agent node's provider session.

Claude Code starts with a spending cap from the run's remaining budget, a stop at that cap
ends the run at its cost limit, and usage a provider reports mid-turn can stop the node.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.core.provider_profiles import resolve_provider_settings
from gofer.core.usage_context import ProviderCall, observe_usage
from gofer.rattish.run_service import run_rattish_file
from gofer.rattish.run_usage import (
    COST_LIMIT_EXCEEDED,
    TOKEN_LIMIT_EXCEEDED,
    RunUsageMeter,
)
from gofer.subscriptions import claude_code
from gofer.subscriptions.claude_code import ClaudeCodeSubscription, claude_budget_cap
from gofer.subscriptions.cli_providers import stream_cli


def _call(
    tokens: int | None = None,
    cost: float | None = None,
    *,
    invocation: str | None = None,
    partial: bool = False,
    **extra: Any,
) -> ProviderCall:
    return ProviderCall(
        "claude_code",
        None,
        {"total_tokens": tokens} if tokens is not None else {},
        cost,
        partial,
        False,
        invocation_id=invocation,
        **extra,
    )


def _command(meter: RunUsageMeter | None, *, extra_args: list[str] | None = None) -> list[str]:
    settings = resolve_provider_settings(agent_subscription="claude_code", profile_name=None)
    if extra_args is not None:
        settings = settings.model_copy(update={"extra_args": extra_args})
    subscription = ClaudeCodeSubscription()
    if meter is None:
        return subscription._build_command("-", [], [], [], settings)
    with observe_usage(meter.observer("work", 1)):
        return subscription._build_command("-", [], [], [], settings)


def _budget_flag(command: list[str]) -> str | None:
    if "--max-budget-usd" not in command:
        return None
    return command[command.index("--max-budget-usd") + 1]


def test_cap_is_the_remaining_budget_less_headroom() -> None:
    assert claude_budget_cap(1.0) == pytest.approx(0.9)
    assert claude_budget_cap(0.123456) == pytest.approx(0.1111)
    # A spent budget still passes a cap Claude Code accepts (it ignores 0).
    assert claude_budget_cap(0) == pytest.approx(0.0001)


def test_claude_code_gets_the_runs_remaining_budget_only_inside_a_capped_run() -> None:
    meter = RunUsageMeter("run-1", max_cost_usd=1.0)
    meter.add("earlier", 1, _call(10, 0.4))

    assert _budget_flag(_command(meter)) == "0.5400"
    assert _budget_flag(_command(None)) is None
    assert _budget_flag(_command(RunUsageMeter("run-2", max_tokens=100))) is None


def test_a_smaller_cap_from_the_profile_stays_and_a_larger_one_is_overridden() -> None:
    meter = RunUsageMeter("run-1", max_cost_usd=1.0)

    smaller = _command(meter, extra_args=["--max-budget-usd", "0.25"])
    assert smaller.count("--max-budget-usd") == 1
    assert _budget_flag(smaller) == "0.25"

    larger = _command(meter, extra_args=["--max-budget-usd=5"])
    # Claude Code reads the last value, which is the run's.
    assert larger[-2:] == ["--max-budget-usd", "0.9000"]


def test_the_tightest_budget_wins_between_a_child_run_and_its_caller() -> None:
    parent = RunUsageMeter("parent", max_cost_usd=0.5)
    parent.add("call-child", 1, _call(1, 0.3))
    child = RunUsageMeter("child", max_cost_usd=10, parent=parent.observer("call-child", 1))

    budget = child.cost_budget()

    assert budget is not None
    assert budget.run_id == "parent"
    assert budget.remaining_usd == pytest.approx(0.2)


def test_a_stop_at_the_runs_cap_is_the_runs_cost_limit() -> None:
    meter = RunUsageMeter("run-1", max_cost_usd=1.0)

    meter.add(
        "work",
        1,
        _call(
            100,
            0.93,
            cost_cap_usd=0.9,
            cost_cap_run_id="run-1",
            stopped_at_cost_cap=True,
        ),
    )

    reached = meter.limit_reached()
    assert reached is not None
    assert reached["code"] == COST_LIMIT_EXCEEDED
    assert "Claude Code stopped node 'work' at its spending cap of $0.90" in reached["message"]
    assert reached["details"]["provider_cap_usd"] == pytest.approx(0.9)


def test_a_stop_at_a_cap_the_run_did_not_set_is_not_its_limit() -> None:
    meter = RunUsageMeter("run-1", max_cost_usd=1.0)

    meter.add("work", 1, _call(100, 0.2, stopped_at_cost_cap=True))

    assert meter.limit_reached() is None


def test_running_calls_count_toward_a_limit_and_their_final_record_replaces_them() -> None:
    meter = RunUsageMeter("run-1", max_tokens=1000)
    observer = meter.observer("work", 1)

    assert observer.observe_live(_call(400, invocation="a", partial=True)) is False
    assert observer.observe_live(_call(500, invocation="b", partial=True)) is False
    # Both calls report more; together they pass the limit.
    assert observer.observe_live(_call(600, invocation="a", partial=True)) is True

    reached = meter.limit_reached()
    assert reached is not None
    assert reached["code"] == TOKEN_LIMIT_EXCEEDED
    assert "while its call was running" in reached["message"]
    assert reached["details"]["used_tokens"] == 1100
    meter.add("work", 1, _call(650, invocation="a"))
    meter.add("work", 1, _call(500, invocation="b"))
    assert meter.snapshot()["total_tokens"] == 1150


def test_running_calls_reduce_the_budget_a_new_call_starts_with() -> None:
    meter = RunUsageMeter("run-1", max_cost_usd=1.0)
    meter.observer("work", 1).observe_live(_call(10, 0.25, invocation="a", partial=True))

    budget = meter.cost_budget()

    assert budget is not None
    assert budget.remaining_usd == pytest.approx(0.75)


def test_a_child_runs_running_call_counts_for_the_caller() -> None:
    parent = RunUsageMeter("parent", max_tokens=100)
    child = RunUsageMeter("child", parent=parent.observer("call-child", 1))

    assert child.observer("inner", 1).observe_live(_call(150, invocation="x", partial=True))
    reached = parent.limit_reached()
    assert reached is not None and reached["details"]["node_id"] == "call-child"


# A stand-in for the claude executable. It records its arguments and plays a scenario.
_FAKE_CLAUDE = """#!{python}
import json, sys, time
sys.stdin.read()
root = {root!r}
with open(root + "/argv.json", "w") as handle:
    json.dump(sys.argv[1:], handle)
scenario = json.load(open(root + "/scenario.json"))
for record in scenario["records"]:
    print(json.dumps(record), flush=True)
time.sleep(scenario.get("sleep", 0))
for record in scenario.get("after", []):
    print(json.dumps(record), flush=True)
sys.exit(scenario.get("exit", 0))
"""


def _assistant(identity: str, input_tokens: int, output_tokens: int) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {
            "id": identity,
            "role": "assistant",
            "content": [{"type": "text", "text": f"Working on {identity}"}],
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        },
    }


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "fake-claude"
    root.mkdir()
    script = root / "claude"
    script.write_text(_FAKE_CLAUDE.format(python=sys.executable, root=str(root)))
    script.chmod(0o755)
    monkeypatch.setattr(claude_code, "resolve_provider_executable", lambda _id: str(script))
    return root


def _workflow(tmp_path: Path, limits: str) -> Path:
    source = tmp_path / "project" / "workflow.rattish"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        f"""Rattish: 1

Workflow:
  name: Agent limits
{limits}
Node work:
  type: agent
  provider: claude-code
  prompt: Do the work.
  to: after

Node after:
  type: agent
  provider: claude-code
  prompt: Never reached.
  needs: work
""",
        encoding="utf-8",
    )
    return source


async def _run(source: Path, tmp_path: Path) -> dict[str, Any]:
    result = await run_rattish_file(
        source,
        data_dir=tmp_path / "data",
        subscriptions={"claude_code": ClaudeCodeSubscription()},
    )
    return result.document


@pytest.mark.anyio
async def test_claude_codes_budget_stop_ends_the_run_at_its_cost_limit(
    tmp_path: Path, fake_claude: Path
) -> None:
    (fake_claude / "scenario.json").write_text(
        json.dumps(
            {
                "records": [
                    {"type": "system", "subtype": "init"},
                    _assistant("m1", 100, 50),
                    {
                        "type": "result",
                        "subtype": "error_max_budget_usd",
                        "is_error": True,
                        "total_cost_usd": 0.47,
                        "usage": {"input_tokens": 100, "output_tokens": 50},
                        "errors": ["Reached maximum budget ($0.45)"],
                    },
                ],
                "exit": 1,
            }
        )
    )

    document = await _run(_workflow(tmp_path, "  max-cost-usd: 0.50\n"), tmp_path)

    argv = json.loads((fake_claude / "argv.json").read_text())
    assert argv[argv.index("--max-budget-usd") + 1] == "0.4500"
    assert document["status"] == "failed"
    error = document["error"]
    assert error["kind"] == "budget"
    assert error["code"] == COST_LIMIT_EXCEEDED
    assert "spending cap of $0.45" in error["message"]
    assert "max-cost-usd limit of $0.50" in error["message"]
    # The node itself reports the budget error, not a generic provider failure.
    [record] = document["runs"]
    assert record["node_id"] == "work"
    assert record["error"]["code"] == COST_LIMIT_EXCEEDED
    assert document["usage"]["cost_usd"] == pytest.approx(0.47)


@pytest.mark.anyio
async def test_tokens_reported_mid_turn_stop_the_node_at_max_tokens(
    tmp_path: Path, fake_claude: Path
) -> None:
    (fake_claude / "scenario.json").write_text(
        json.dumps(
            {
                "records": [
                    _assistant("m1", 300, 100),
                    # Claude Code repeats a message's usage on each of its content blocks.
                    _assistant("m1", 300, 100),
                    _assistant("m2", 400, 200),
                ],
                # Without the live count, the run would wait for this whole turn.
                "sleep": 60,
                "after": [{"type": "result", "subtype": "success", "result": "late"}],
            }
        )
    )

    started = time.monotonic()
    document = await _run(_workflow(tmp_path, "  max-tokens: 500\n"), tmp_path)

    assert time.monotonic() - started < 30
    argv = json.loads((fake_claude / "argv.json").read_text())
    assert "--max-budget-usd" not in argv
    error = document["error"]
    assert error["kind"] == "budget"
    assert error["code"] == TOKEN_LIMIT_EXCEEDED
    assert "stopped node 'work' while its call was running" in error["message"]
    assert error["details"]["used_tokens"] == 1000
    [record] = document["runs"]
    assert record["error"]["code"] == TOKEN_LIMIT_EXCEEDED
    usage = document["usage"]
    assert usage["total_tokens"] == 1000
    assert usage["partial_calls"] == 1


@pytest.mark.anyio
async def test_a_claude_code_run_under_its_limits_passes_with_the_cap_set(
    tmp_path: Path, fake_claude: Path
) -> None:
    (fake_claude / "scenario.json").write_text(
        json.dumps(
            {
                "records": [
                    _assistant("m1", 10, 5),
                    {
                        "type": "result",
                        "subtype": "success",
                        "result": "done",
                        "total_cost_usd": 0.01,
                        "usage": {"input_tokens": 10, "output_tokens": 5},
                    },
                ]
            }
        )
    )

    document = await _run(_workflow(tmp_path, "  max-cost-usd: 1\n  max-tokens: 1000\n"), tmp_path)

    assert document["status"] == "passed", document.get("error")
    assert document["usage"]["calls"] == 2
    assert document["usage"]["cost_usd"] == pytest.approx(0.02)
    argv = json.loads((fake_claude / "argv.json").read_text())
    # The second node started with what the first left of the $1 budget.
    assert argv[argv.index("--max-budget-usd") + 1] == "0.8910"


@pytest.mark.anyio
async def test_cli_providers_pass_on_usage_as_their_steps_finish(tmp_path: Path) -> None:
    script = tmp_path / "opencode"
    steps = [
        {
            "type": "step_finish",
            "sessionID": "s1",
            "part": {"id": f"p{index}", "messageID": "m", "tokens": {"input": 10, "output": 5}},
        }
        for index in (1, 2)
    ]
    script.write_text(
        f"#!{sys.executable}\nimport json, time\n"
        + "".join(f"print(json.dumps({step!r}), flush=True)\ntime.sleep(0.3)\n" for step in steps)
    )
    script.chmod(0o755)
    reported: list[dict[str, Any]] = []

    async for _event in stream_cli(
        [str(script)],
        "opencode",
        cwd=tmp_path,
        env=dict(os.environ),
        on_usage=reported.append,
    ):
        pass

    assert [item["total_tokens"] for item in reported] == [15, 30]


def test_a_failed_call_that_reported_nothing_leaves_retries_and_fallback_to_run(
    tmp_path: Path,
) -> None:
    # A rate-limited or signed-out provider answers with an error and reports no usage.
    # Ending the run for "didn't report its cost" would hide that error and skip fallback.
    meter = RunUsageMeter("run-1", max_tokens=10_000, max_cost_usd=1.0)
    observer = meter.observer("work", 1)
    observer.observe(_call(failed=True))
    assert meter.stop_requested() is False and meter.limit_reached() is None

    # A failed call that did report some use is still held to the limits.
    observer.observe(_call(tokens=50, failed=True))
    limit = meter.limit_reached()
    assert limit is not None and limit["code"] == "RATTISH_COST_LIMIT_UNCHECKABLE"

    # A completed call that reported nothing still ends the run to stay safe.
    completed = RunUsageMeter("run-2", max_cost_usd=1.0)
    completed.observer("work", 1).observe(_call())
    limit = completed.limit_reached()
    assert limit is not None and limit["code"] == "RATTISH_COST_LIMIT_UNCHECKABLE"

    # The usage ledger tells the run which calls failed.
    from gofer.core.usage_ledger import record_invocation

    seen: list[ProviderCall] = []
    ledger_meter = RunUsageMeter("run-3", max_cost_usd=1.0)
    real = ledger_meter.observer("work", 1)

    def observe(call: ProviderCall) -> None:
        seen.append(call)
        real.observe(call)

    spy = type(real)(
        run_id=real.run_id,
        node_id=real.node_id,
        observe=observe,
        stop_requested=real.stop_requested,
    )
    with observe_usage(spy):
        for status in ("failed", "cancelled", "completed"):
            record_invocation(
                invocation_id=f"call-{status}",
                provider="openai_api",
                metadata={},
                status=status,
                data_dir=tmp_path,
            )
    assert [call.failed for call in seen] == [True, True, False]
