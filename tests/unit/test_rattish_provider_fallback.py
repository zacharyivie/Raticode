"""Fallback providers for Agent and Common LLM Task nodes.

The compiler resolves each entry against its own provider contract, preflight checks
every entry, and the runtime moves to the next entry only after a provider availability
error. The run record says which provider answered and why the step switched.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from gofer.core.agent import AgentResult
from gofer.core.provider_profiles import ResolvedProviderSettings
from gofer.rattish.artifacts import compile_rattish_source, rattish_asset_root
from gofer.rattish.diagnostics import RattishError
from gofer.rattish.preflight import run_preflight
from gofer.rattish.provider_fallback import (
    availability_reason,
    fallback_reason,
    switch_message,
)
from gofer.rattish.run_service import resume_rattish_run, run_rattish_file
from gofer.rattish.runtime import RuntimeErrorInfo
from gofer.subscriptions.base import Subscription
from gofer.subscriptions.usage import track_invocation

Script = tuple[bool, str, str | None, int, dict[str, Any]]
RATE_LIMITED: Script = (False, "", "API Error: 429 rate limit exceeded, try again later", 1, {})


class ScriptedProvider(Subscription):
    """A provider that answers each call from a script and goes through the usage ledger."""

    def __init__(
        self,
        provider: str,
        script: list[Script] | None = None,
        *,
        available: bool = True,
    ) -> None:
        self.provider = provider
        self.script = list(script or [])
        self.available = available
        self.calls: list[ResolvedProviderSettings | None] = []

    def _build_command(self, *args: Any, **kwargs: Any) -> list[str]:
        return ["fake"]

    def is_available(self) -> bool:
        return self.available

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
        self.calls.append(provider_settings)
        success, output, message, exit_code, usage = (
            self.script.pop(0) if self.script else (True, f"{self.provider} answer", None, 0, {})
        )
        return AgentResult(
            agent_id="",
            success=success,
            output=output,
            exit_code=exit_code,
            duration_seconds=0,
            message=message,
            usage_metadata=usage,
        )


def _write(tmp_path: Path, text: str) -> Path:
    source = tmp_path / "project" / "workflow.rattish"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(text, encoding="utf-8")
    return source


def _agent_source(
    tmp_path: Path,
    *,
    workflow_fields: str = "",
    node_fields: str = "",
    fallback: str = "    -\n      provider: codex\n      model: gpt-5-fallback\n",
) -> Path:
    return _write(
        tmp_path,
        "Rattish: 1\n\nWorkflow:\n  name: Fallback\n"
        + workflow_fields
        + "\nNode ask:\n  type: agent\n  provider: claude-code\n  prompt: Say hi.\n"
        + node_fields
        + ("  fallback:\n" + fallback if fallback else ""),
    )


def _compile(source: Path) -> Any:
    return compile_rattish_source(
        source.read_text(encoding="utf-8"), source, project_root=source.parent
    )


async def _run(source: Path, tmp_path: Path, **providers: ScriptedProvider) -> dict[str, Any]:
    result = await run_rattish_file(source, data_dir=tmp_path / "data", subscriptions=providers)
    document = result.document
    schema = json.loads(
        (rattish_asset_root() / "schemas" / "run.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(json.loads(json.dumps(document)))
    return document


def _events(document: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [event for event in document["events"] if event["type"] == kind]


# Compiler


def test_each_entry_takes_defaults_from_its_own_provider_contract(tmp_path: Path) -> None:
    source = _agent_source(
        tmp_path,
        fallback=(
            "    -\n      provider: Codex\n      effort: low\n"
            "    -\n      provider: claude-code\n      model: claude-haiku-5\n"
        ),
    )

    node = _compile(source).ir["nodes"][0]

    resolution = node["resolutions"]["provider"]
    codex, claude = resolution["fallback"]
    assert codex["provider_id"] == "codex"
    assert codex["model"] != resolution["model"]  # codex's default, not Claude's
    assert codex["effort"] == "low"
    assert codex["profile"] is None
    assert claude["model"] == "claude-haiku-5"
    assert claude["effort"] == resolution["effort"]
    assert node["configuration"]["fallback"][0] == {
        "provider": "codex",
        "profile": None,
        "model": codex["model"],
        "effort": "low",
    }


def test_a_fallback_provider_without_a_contract_does_not_compile(tmp_path: Path) -> None:
    source = _agent_source(tmp_path, fallback="    -\n      provider: nobody\n")

    with pytest.raises(RattishError) as caught:
        _compile(source)

    unresolved = [
        item
        for item in caught.value.diagnostics
        if item.code == "RATTISH_PROVIDER_CONTRACT_UNRESOLVED"
    ]
    assert unresolved and unresolved[0].details == {
        "node": "ask",
        "provider": "nobody",
        "fallback": 0,
    }


@pytest.mark.parametrize(
    "fallback",
    [
        "    -\n      model: gpt-5\n",
        "    -\n      provider: codex\n      tools: []\n",
        "    - codex\n",
        "".join(f"    -\n      provider: codex\n      model: m{index}\n" for index in range(5)),
    ],
    ids=["no-provider", "unknown-field", "not-a-map", "too-many"],
)
def test_malformed_fallback_entries_do_not_compile(tmp_path: Path, fallback: str) -> None:
    with pytest.raises(RattishError) as caught:
        _compile(_agent_source(tmp_path, fallback=fallback))

    assert any(item.severity == "error" for item in caught.value.diagnostics)


def test_an_entry_that_repeats_the_primary_warns(tmp_path: Path) -> None:
    source = _agent_source(tmp_path, fallback="    -\n      provider: claude-code\n")

    result = _compile(source)

    assert [item.code for item in result.diagnostics] == ["RATTISH_DUPLICATE_PROVIDER_FALLBACK"]


def test_common_llm_task_accepts_a_fallback_list(tmp_path: Path) -> None:
    source = _write(
        tmp_path,
        "Rattish: 1\n\nWorkflow:\n  name: Summary\n\nNode sum:\n  type: common-llm-task\n"
        "  task: summarize\n  target: Some text\n  provider: codex\n"
        "  fallback:\n    -\n      provider: claude-code\n      effort: low\n",
    )

    resolution = _compile(source).ir["nodes"][0]["resolutions"]["provider"]

    assert [(item["provider_id"], item["effort"]) for item in resolution["fallback"]] == [
        ("claude-code", "low")
    ]


def test_steps_without_a_fallback_compile_an_empty_list(tmp_path: Path) -> None:
    node = _compile(_agent_source(tmp_path, fallback="")).ir["nodes"][0]

    assert node["configuration"]["fallback"] == []
    assert node["resolutions"]["provider"]["fallback"] == []


# Classifying failures


@pytest.mark.parametrize(
    ("text", "exit_code", "reason"),
    [
        ("API Error: 429 Too Many Requests", 1, "rate_limit"),
        ("You've hit your usage limit. Resets at 5pm.", 1, "rate_limit"),
        ("You exceeded your current quota (insufficient_quota)", 1, "rate_limit"),
        ('API Error: 529 {"type":"overloaded_error"}', 1, "overloaded"),
        ("503 Service Unavailable", 1, "overloaded"),
        ("Invalid API key · Please run /login", 1, "signed_out"),
        ("OAuth token has expired. Please log in again.", 1, "signed_out"),
        ("Not logged in", 1, "signed_out"),
        ("sh: claude: command not found", 127, "not_installed"),
        ("The tests failed: 3 assertions did not hold.", 1, None),
        ("Error: could not parse the configuration file", 2, None),
        ("", 1, None),
    ],
)
def test_availability_reasons(text: str, exit_code: int, reason: str | None) -> None:
    assert availability_reason(text, exit_code) == reason


def test_only_provider_errors_with_an_availability_reason_may_fall_back() -> None:
    assert fallback_reason(RuntimeErrorInfo("provider", "X", "", {"availability": "overloaded"}))
    assert fallback_reason(RuntimeErrorInfo("provider", "X", "", {})) is None
    assert (
        fallback_reason(RuntimeErrorInfo("budget", "X", "", {"availability": "rate_limit"})) is None
    )
    assert (
        fallback_reason(RuntimeErrorInfo("output_validation", "X", "", {"availability": "x"}))
        is None
    )
    assert fallback_reason(None) is None


def test_switch_message_names_both_providers_and_the_reason() -> None:
    message = switch_message(
        {
            "from": {"provider": "claude-code", "profile": None, "model": "a", "effort": "high"},
            "to": {"provider": "codex", "profile": "work", "model": "b", "effort": "low"},
            "reason": "rate_limit",
        }
    )

    assert message == (
        "Switched from claude-code (a) to codex profile work (b): rate limit or allowance used up."
    )


# Running


@pytest.mark.anyio
async def test_after_retries_a_rate_limit_moves_to_the_next_entry(tmp_path: Path) -> None:
    claude = ScriptedProvider("claude_code", [RATE_LIMITED, RATE_LIMITED])
    codex = ScriptedProvider("codex")
    source = _agent_source(tmp_path, node_fields="  retry-count: 1\n")

    document = await _run(source, tmp_path, claude_code=claude, codex=codex)

    assert document["status"] == "passed"
    assert len(claude.calls) == 2  # the first try and its one retry
    assert len(codex.calls) == 1
    assert codex.calls[0] is not None and codex.calls[0].model == "gpt-5-fallback"
    (record,) = document["runs"]
    assert record["output"] == "codex answer"
    assert record["provider"] == {
        "provider": "codex",
        "profile": None,
        "model": "gpt-5-fallback",
        "effort": record["provider"]["effort"],
    }
    (switch,) = record["fallbacks"]
    assert switch["from"]["provider"] == "claude-code"
    assert switch["to"]["provider"] == "codex"
    assert switch["reason"] == "rate_limit"
    assert "429" in switch["message"]
    (event,) = _events(document, "node_fell_back")
    assert event["fallback_reason"] == "rate_limit"
    assert event["provider"]["provider"] == "codex"
    assert event["from_provider"]["provider"] == "claude-code"
    assert event["message"].startswith("Switched from claude-code")
    assert [item["attempt"] for item in _events(document, "node_started")] == [1, 2, 3]


@pytest.mark.anyio
async def test_a_provider_that_is_not_installed_is_skipped_without_retries(
    tmp_path: Path,
) -> None:
    claude = ScriptedProvider("claude_code", available=False)
    codex = ScriptedProvider("codex")
    source = _agent_source(tmp_path, node_fields="  retry-count: 2\n")

    document = await _run(source, tmp_path, claude_code=claude, codex=codex)

    assert document["status"] == "passed"
    assert claude.calls == []
    assert len(_events(document, "node_retried")) == 0
    assert document["runs"][0]["fallbacks"][0]["reason"] == "not_installed"
    # Preflight let the run start because a fallback provider is installed, and warned.
    warnings = [
        item
        for item in document["diagnostics"]
        if item["code"] == "RATTISH_PREFLIGHT_FALLBACK_PROVIDER_UNAVAILABLE"
    ]
    assert warnings and warnings[0]["severity"] == "warning"
    assert warnings[0]["details"]["primary_missing"] is True


@pytest.mark.anyio
async def test_the_last_entry_failing_fails_the_step_with_every_switch_recorded(
    tmp_path: Path,
) -> None:
    signed_out: Script = (False, "", "Not logged in · Please run /login", 1, {})
    claude = ScriptedProvider("claude_code", [RATE_LIMITED])
    codex = ScriptedProvider("codex", [signed_out])
    source = _agent_source(tmp_path)

    document = await _run(source, tmp_path, claude_code=claude, codex=codex)

    assert document["status"] == "failed"
    (record,) = document["runs"]
    assert record["provider"]["provider"] == "codex"
    assert [item["reason"] for item in record["fallbacks"]] == ["rate_limit"]
    assert record["error"]["details"]["availability"] == "signed_out"


@pytest.mark.anyio
async def test_task_failures_never_fall_back(tmp_path: Path) -> None:
    claude = ScriptedProvider("claude_code", [(False, "The tests still fail.", None, 1, {})])
    codex = ScriptedProvider("codex")

    document = await _run(_agent_source(tmp_path), tmp_path, claude_code=claude, codex=codex)

    assert document["status"] == "failed"
    assert codex.calls == []
    assert "fallbacks" not in document["runs"][0]
    assert document["runs"][0]["provider"]["provider"] == "claude-code"
    assert _events(document, "node_fell_back") == []


@pytest.mark.anyio
async def test_schema_repair_failures_never_fall_back(tmp_path: Path) -> None:
    claude = ScriptedProvider(
        "claude_code", [(True, "not json", None, 0, {}), (True, "still not json", None, 0, {})]
    )
    codex = ScriptedProvider("codex")
    source = _agent_source(
        tmp_path,
        node_fields=(
            "  repair-attempts: 1\n  output-schema:\n    type: object\n"
            "    properties:\n      ok:\n        type: boolean\n    required:\n      - ok\n"
        ),
    )

    document = await _run(source, tmp_path, claude_code=claude, codex=codex)

    assert document["status"] == "failed"
    assert document["runs"][0]["error"]["kind"] == "output_validation"
    assert codex.calls == []


@pytest.mark.anyio
async def test_a_budget_stop_never_falls_back(tmp_path: Path) -> None:
    over_budget = (False, "", "API Error: 429 rate limit", 1, {"total_cost_usd": 0.6})
    claude = ScriptedProvider("claude_code", [over_budget])
    codex = ScriptedProvider("codex")
    source = _agent_source(tmp_path, workflow_fields="  max-cost-usd: 0.5\n")

    document = await _run(source, tmp_path, claude_code=claude, codex=codex)

    assert document["status"] == "failed"
    assert document["error"]["kind"] == "budget"
    assert codex.calls == []
    assert _events(document, "node_fell_back") == []


@pytest.mark.anyio
async def test_the_runs_limits_cover_every_providers_attempts(tmp_path: Path) -> None:
    claude = ScriptedProvider(
        "claude_code",
        [(False, "", "API Error: 529 overloaded", 1, {"total_cost_usd": 0.3, "input_tokens": 40})],
    )
    codex = ScriptedProvider(
        "codex", [(True, "answer", None, 0, {"total_cost_usd": 0.3, "input_tokens": 70})]
    )
    source = _agent_source(
        tmp_path,
        workflow_fields="  max-cost-usd: 0.5\n",
        fallback="    -\n      provider: codex\n    -\n      provider: codex\n      model: other\n",
    )

    document = await _run(source, tmp_path, claude_code=claude, codex=codex)

    usage = document["usage"]
    assert usage["calls"] == 2
    assert usage["cost_usd"] == pytest.approx(0.6)
    assert usage["nodes"]["ask"]["input_tokens"] == 110
    # The fallback's call crossed the run's limit, so the run ends at its cost limit.
    assert document["error"]["kind"] == "budget"
    assert document["error"]["code"] == "RATTISH_COST_LIMIT_EXCEEDED"


@pytest.mark.anyio
async def test_common_llm_task_falls_back_too(tmp_path: Path) -> None:
    source = _write(
        tmp_path,
        "Rattish: 1\n\nWorkflow:\n  name: Summary\n\nNode sum:\n  type: common-llm-task\n"
        "  task: summarize\n  target: Some text\n  provider: codex\n"
        "  fallback:\n    -\n      provider: claude-code\n",
    )
    codex = ScriptedProvider("codex", [(False, "", "503 Service Unavailable", 1, {})])
    claude = ScriptedProvider("claude_code")

    document = await _run(source, tmp_path, claude_code=claude, codex=codex)

    assert document["status"] == "passed"
    assert document["runs"][0]["provider"]["provider"] == "claude-code"
    assert document["runs"][0]["fallbacks"][0]["reason"] == "overloaded"


@pytest.mark.anyio
async def test_steps_without_a_fallback_record_their_provider(tmp_path: Path) -> None:
    claude = ScriptedProvider("claude_code")

    document = await _run(_agent_source(tmp_path, fallback=""), tmp_path, claude_code=claude)

    record = document["runs"][0]
    assert record["provider"]["provider"] == "claude-code"
    assert "fallbacks" not in record


@pytest.mark.anyio
async def test_a_resumed_run_keeps_the_reused_steps_provider(tmp_path: Path) -> None:
    source = _write(
        tmp_path,
        "Rattish: 1\n\nWorkflow:\n  name: Resume\n\nNode ask:\n  type: agent\n"
        "  provider: claude-code\n  prompt: Say hi.\n  to: check\n"
        "  fallback:\n    -\n      provider: codex\n\n"
        "Node check:\n  type: bash-command\n  command: exit 1\n  needs: ask\n",
    )
    claude = ScriptedProvider("claude_code", [RATE_LIMITED])
    codex = ScriptedProvider("codex")
    first = await run_rattish_file(
        source,
        data_dir=tmp_path / "data",
        subscriptions={"claude_code": claude, "codex": codex},
    )
    assert first.document["status"] == "failed"

    resumed = await resume_rattish_run(
        source,
        first.document,
        data_dir=tmp_path / "data",
        subscriptions={"claude_code": claude, "codex": codex},
    )

    reused = next(item for item in resumed.document["runs"] if item["node_id"] == "ask")
    assert reused.get("reused_from_run_id") == first.document["run_id"]
    assert reused["provider"]["provider"] == "codex"
    assert reused["fallbacks"][0]["reason"] == "rate_limit"


# Preflight


def _preflight(source: Path, tmp_path: Path, **providers: ScriptedProvider) -> list[Any]:
    return list(
        run_preflight(
            _compile(source).ir, data_dir=tmp_path / "data", subscriptions=providers
        ).diagnostics
    )


def test_preflight_fails_only_when_no_provider_in_the_chain_is_installed(tmp_path: Path) -> None:
    source = _agent_source(tmp_path)

    none_installed = _preflight(
        source,
        tmp_path,
        claude_code=ScriptedProvider("claude_code", available=False),
        codex=ScriptedProvider("codex", available=False),
    )
    fallback_missing = _preflight(
        source,
        tmp_path,
        claude_code=ScriptedProvider("claude_code"),
        codex=ScriptedProvider("codex", available=False),
    )
    all_installed = _preflight(
        source,
        tmp_path,
        claude_code=ScriptedProvider("claude_code"),
        codex=ScriptedProvider("codex"),
    )

    assert [(item.code, item.severity) for item in none_installed] == [
        ("RATTISH_PREFLIGHT_PROVIDER_UNAVAILABLE", "error")
    ]
    assert none_installed[0].details["providers"] == ["claude-code", "codex"]
    assert [(item.code, item.severity) for item in fallback_missing] == [
        ("RATTISH_PREFLIGHT_FALLBACK_PROVIDER_UNAVAILABLE", "warning")
    ]
    assert fallback_missing[0].details["fallback"] == [0]
    assert all_installed == []


def test_preflight_checks_each_fallback_entrys_profile(tmp_path: Path) -> None:
    source = _agent_source(
        tmp_path, fallback="    -\n      provider: codex\n      profile: missing\n"
    )

    diagnostics = _preflight(
        source,
        tmp_path,
        claude_code=ScriptedProvider("claude_code"),
        codex=ScriptedProvider("codex"),
    )

    (problem,) = diagnostics
    assert problem.code == "RATTISH_PREFLIGHT_PROFILE_UNAVAILABLE"
    assert problem.severity == "error"
    assert problem.details["fallback"] == 0
    assert problem.message.startswith("Fallback entry 1 (codex):")


# Surfaces: Studio, run list, phone and command line


@pytest.mark.anyio
async def test_every_surface_names_the_answering_provider_and_the_switch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from gofer.cli.commands.rattish import _emit_run
    from gofer.cli.commands.workflows import _all_runs_text
    from gofer.devices.workflows import _provider_lines
    from gofer.ui.api import _rattish_artifact_ui_payload
    from gofer.ui.workflow_runs import _summary_of, fallback_rows

    claude = ScriptedProvider("claude_code", [RATE_LIMITED])
    codex = ScriptedProvider("codex")
    source = _agent_source(tmp_path)
    result = await run_rattish_file(
        source,
        data_dir=tmp_path / "data",
        subscriptions={"claude_code": claude, "codex": codex},
    )
    document = result.document
    primary_model = document["runs"][0]["fallbacks"][0]["from"]["model"]
    switch = (
        f"Switched from claude-code ({primary_model}) to codex (gpt-5-fallback): "
        "rate limit or allowance used up."
    )

    studio = _rattish_artifact_ui_payload(document, result.path)
    node = studio["runNodes"]["ask"]
    assert node["provider"]["provider"] == "codex"
    assert node["fallbacks"][0]["reason"] == "rate_limit"
    retried = [event for event in studio["runEvents"] if event["type"] == "node_fell_back"]
    assert retried and retried[0]["status"] == "retried"
    assert retried[0]["message"] == switch

    rows = fallback_rows(_summary_of(document))
    assert rows == [
        {
            "nodeId": "ask",
            "from": document["runs"][0]["fallbacks"][0]["from"],
            "to": document["runs"][0]["fallbacks"][0]["to"],
            "reason": "rate_limit",
            "line": f"ask: {switch}",
        }
    ]
    listed = _all_runs_text(
        {
            "runs": [
                {"id": "r-1", "status": "success", "workflowName": "Fallback", "fallbacks": rows}
            ]
        }
    )
    assert "success (switched provider)" in listed
    assert f"r-1  ask: {switch}" in listed

    assert _provider_lines(node) == [switch, "Answered by codex (gpt-5-fallback)."]
    assert _provider_lines({"status": "success", "provider": node["provider"]}) == []

    _emit_run(result, "text")
    printed = capsys.readouterr().out
    assert f"ask: {switch}" in printed
    assert "ask: answered by codex (gpt-5-fallback)" in printed
