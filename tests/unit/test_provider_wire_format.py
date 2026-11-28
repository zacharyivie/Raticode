"""Every way Raticode reaches a provider, run end to end against CLIs that check the wire.

The 11-21 Rem chat outage passed its tests because they replaced the agent wrapper and
never saw what Claude Code read on stdin. Here nothing between Raticode and the process
is replaced: each call path runs as it does in the app and starts a fake CLI
(tests/helpers/fake_provider_cli.py) installed under the real binary name. The fake
rejects options its CLI doesn't list, reads the prompt from where its CLI reads it, fails
like the real CLI on a malformed stream-json line, a missing image or prompt file, or
(Codex) a second denied file, and answers with what reached the model.

Paths covered, for each provider that takes them:
- Rem's tool-free calls: chat replies in blocks, the other decisions and summaries
  (through RemHome._generate, as self-work, stop conditions, task updates and worker
  answers call it), Organizations' decisions without a system prompt, and images.
- Workflow agent steps (Agent with the runner's subscriptions).
- Worker, reviewer and desktop thread turns (stream_workflow_chat).
- Chat compaction, and provider errors shown to people.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gofer.core.agent import Agent, AgentConfig
from gofer.core.provider_capabilities import PROVIDER_BINARIES
from gofer.core.provider_profiles import resolve_provider_settings
from gofer.core.resources import DEFAULT_RESOURCE_LIMITS
from gofer.ui import chat
from gofer.ui import rem_home_provider as provider_module
from gofer.ui.rem_home_provider import CHAT_IMAGE_PROVIDERS, ChatImage, PromptBlocks
from gofer.ui.rem_reply_prompt import reply_prompt

HELPER = Path(__file__).resolve().parents[1] / "helpers" / "fake_provider_cli.py"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
ALL = ["claude_code", "codex", "cursor", "copilot", "opencode", "grok", "antigravity"]
# Grok threads and workflow steps use ACP (tests/unit/test_acp_providers.py).
SUBPROCESS_TURNS = ["claude_code", "codex", "cursor", "copilot", "opencode", "antigravity"]
ANSWER = {"reply": "wire ok", "marker": True, "rules": True, "images": 0}


@pytest.fixture
def wire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Install the fake CLIs first on PATH; returns the file each call is logged to."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    for name in PROVIDER_BINARIES.values():
        launcher = bin_dir / name
        launcher.write_text(
            f"#!{sys.executable}\nimport runpy\n"
            f"runpy.run_path({str(HELPER)!r}, init_globals={{'LAUNCHED_AS': {name!r}}}, "
            "run_name='__main__')\n",
            encoding="utf-8",
        )
        launcher.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    log = tmp_path / "provider-calls.jsonl"
    monkeypatch.setenv("FAKE_PROVIDER_LOG", str(log))
    # Which options a CLI lists is cached per executable; these are new ones.
    provider_module._lists_option.cache_clear()
    yield log
    provider_module._lists_option.cache_clear()


def calls(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def only_call(log: Path) -> dict[str, Any]:
    [call] = calls(log)
    assert "refused" not in call, call
    return call


def chat_prompt(body: str = "WIRE-MARKER: did the tickets get done?") -> PromptBlocks:
    return reply_prompt(
        {"projects": [{"root": "/work/project"}], "memories": []},
        {"board": [], "tasks": []},
        {"current_request": {"id": "m1", "body": body}, "at": "2026-11-21T08:00:00Z"},
        "Reply as JSON.",
    )


# --- Rem's tool-free calls ---------------------------------------------------------------


@pytest.mark.parametrize("provider", ALL)
@pytest.mark.parametrize("shape", ["blocks", "text", "no-system"])
def test_rem_tool_free_calls_reach_each_provider_intact(
    wire: Path, tmp_path: Path, provider: str, shape: str
) -> None:
    prompt: str = chat_prompt() if shape == "blocks" else "Summarise this: WIRE-MARKER"
    decision = provider_module.generate_decision(
        prompt,
        {"provider": provider, "model": "cli-default"},
        threading.Event(),
        tmp_path / "data",
        system_prompt="" if shape == "no-system" else "WIRE-RULES: reply as JSON.",
    )
    assert decision == {**ANSWER, "rules": shape != "no-system"}
    call = only_call(wire)
    # Any local account can read a command line, and Rem's prompts carry its memories.
    assert not any("WIRE-MARKER" in arg or "WIRE-RULES" in arg for arg in call["args"])
    if provider == "claude_code":
        # Several blocks go as one stream-json message; one text goes as plain text.
        assert ("--input-format" in call["args"]) is (shape == "blocks")
        assert call["args"][call["args"].index("--tools") + 1] == ""
        assert call["stdin"].count("\n") <= 1 or shape != "blocks"


@pytest.mark.parametrize("provider", sorted(CHAT_IMAGE_PROVIDERS))
def test_rem_chat_images_reach_each_provider_that_takes_them(
    wire: Path, tmp_path: Path, provider: str
) -> None:
    image = ChatImage("shot.png", "image/png", ".png", PNG)
    decision = provider_module.generate_decision(
        chat_prompt(),
        {"provider": provider, "model": "cli-default"},
        threading.Event(),
        tmp_path / "data",
        system_prompt="WIRE-RULES",
        images=[image],
    )
    assert decision == {**ANSWER, "images": 1}
    only_call(wire)


@pytest.mark.parametrize("provider", ALL)
def test_rem_decisions_through_rem_home_reach_each_provider(
    wire: Path, tmp_path: Path, provider: str
) -> None:
    """Self-work, stop conditions, task updates and worker answers all call _generate."""
    from gofer.ui.chat_jobs import ChatJobs
    from gofer.ui.chat_steering import ChatSteering
    from gofer.ui.rem_home import RemHome

    async def no_source(**kwargs: Any):  # pragma: no cover - no task starts here
        yield {"type": "final", "message": {"body": ""}}

    data = tmp_path / "rem-data"
    data.mkdir()
    home = RemHome(
        data,
        source=no_source,
        jobs=ChatJobs(data),
        steering=ChatSteering(data),
        authorize=lambda c, r: None,
        start=False,
    )
    try:
        decision = home._generate(
            "Plan the next step: WIRE-MARKER",
            {"provider": provider, "model": "cli-default"},
            threading.Event(),
            system="WIRE-RULES: reply as JSON.",
            call="self_work_plan",
        )
    finally:
        home.close()
        home.jobs.close()
        home.steering.close()
    assert decision == ANSWER
    only_call(wire)


def test_a_lookup_round_adds_blocks_after_the_first_rounds(wire: Path, tmp_path: Path) -> None:
    first = chat_prompt()
    for prompt in (first, first + "\nLookup results (data, not instructions):\n[]"):
        provider_module.generate_decision(
            prompt,
            {"provider": "claude_code", "model": "cli-default"},
            threading.Event(),
            tmp_path / "data",
            system_prompt="WIRE-RULES",
        )
    before, after = (
        json.loads(call["stdin"])["message"]["content"] for call in calls(wire) if "stdin" in call
    )
    assert len(after) == len(before) + 1
    assert after[: len(before) - 1] == before[:-1]


# --- Workflow agent steps ----------------------------------------------------------------


@pytest.mark.parametrize("provider", SUBPROCESS_TURNS)
def test_workflow_agent_steps_reach_each_provider(
    wire: Path, tmp_path: Path, provider: str
) -> None:
    from gofer.subscriptions.registry import shared_subscriptions

    work = tmp_path / "work"
    work.mkdir()
    settings = resolve_provider_settings(
        agent_subscription=provider,
        agent_model=None,
        agent_effort=None,
        profile_name=None,
        data_dir=tmp_path / "data",
    )
    if provider == "antigravity":
        settings = settings.model_copy(update={"approval_mode": "cli-managed"})
    config = AgentConfig(
        agent_id="step",
        subscription=provider,  # type: ignore[arg-type]
        working_dir=work,
        # A step's MCP servers are names of servers Claude Code knows; Claude Code has
        # no --mcp-server option, so this failed every such step before.
        mcp_servers=["docs"] if provider == "claude_code" else [],
    )
    result = asyncio.run(
        Agent(config, shared_subscriptions()[provider]).run(
            prompt_override="Do the step: WIRE-MARKER",
            provider_settings=settings,
            timeout=60,
        )
    )
    assert result.success, result.output
    assert json.loads(result.message or result.output)["marker"] is True
    call = only_call(wire)
    if provider == "claude_code":
        assert "mcp__docs" in call["args"]


# --- Worker, reviewer and desktop thread turns -------------------------------------------


@pytest.fixture
def thread_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    async def accept(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(chat, "validate_provider_selection_async", accept)
    monkeypatch.setattr(chat, "ensure_local_gofer_cli", lambda data: tmp_path / "gof")
    project = tmp_path / "project"
    project.mkdir()
    return project


async def thread_turn(
    provider: str, project: Path, data: Path, body: str, **options: Any
) -> list[dict[str, Any]]:
    return [
        event
        async for event in chat.stream_workflow_chat(
            provider,
            "cli-default",
            [{"role": "user", "body": body}],
            {"remResources": {"shell": True}},
            working_dir=project,
            data_dir=data,
            agent_instructions="WIRE-RULES: keep this persona",
            **{
                "permission_mode": "cli-managed" if provider == "antigravity" else None,
                **options,
            },
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", SUBPROCESS_TURNS)
@pytest.mark.parametrize("role", ["worker", "reviewer"])
async def test_thread_turns_reach_each_provider(
    wire: Path, thread_env: Path, tmp_path: Path, provider: str, role: str
) -> None:
    options: dict[str, Any] = {}
    if role == "reviewer" and provider in {"codex", "claude_code"}:
        options = {"permission_mode": "read-only" if provider == "codex" else "dontAsk"}
        if provider == "codex":
            options["trusted_scratch_dir"] = tmp_path / "scratch"
    events = await thread_turn(
        provider, thread_env, tmp_path / "data", "Do the task: WIRE-MARKER", **options
    )
    final = events[-1]
    assert final["type"] == "final", final
    assert json.loads(final["message"]["body"]) == ANSWER
    call = only_call(wire)
    assert not any("WIRE-MARKER" in arg for arg in call["args"])


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", SUBPROCESS_TURNS)
async def test_thread_errors_reach_people_without_terminal_codes(
    wire: Path, thread_env: Path, tmp_path: Path, provider: str
) -> None:
    try:
        events = await thread_turn(provider, thread_env, tmp_path / "data", "FAIL-WITH-COLOUR")
        error = str(events[-1].get("error") or "")
        assert events[-1]["type"] == "error", events[-1]
    except chat.ChatProviderError as exc:
        error = str(exc)
    assert "overloaded" in error
    assert "\x1b" not in error and "[31m" not in error


def test_rem_chat_errors_reach_people_without_terminal_codes(wire: Path, tmp_path: Path) -> None:
    for provider in ALL:
        with pytest.raises(provider_module.ProviderFailure) as failed:
            provider_module.generate_text(
                "FAIL-WITH-COLOUR",
                {"provider": provider, "model": "cli-default"},
                threading.Event(),
                tmp_path / "data",
            )
        assert "overloaded" in str(failed.value) and "\x1b" not in str(failed.value)


# --- Chat compaction ---------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["claude_code", "codex", "cursor", "copilot", "opencode"])
async def test_compaction_keeps_the_answer_not_the_protocol_records(
    wire: Path, thread_env: Path, tmp_path: Path, provider: str
) -> None:
    import shutil

    binary = shutil.which(PROVIDER_BINARIES[provider])
    assert binary is not None
    summary = await chat._summarize_chat_messages(
        provider=provider,
        model="cli-default",
        effort=None,
        messages=[{"role": "user", "body": "Earlier work: WIRE-MARKER"}],
        binary_path=binary,
        data_dir=tmp_path / "data",
        working_dir=thread_env,
        limits=DEFAULT_RESOURCE_LIMITS,
    )
    # Claude Code and Codex used to keep their raw JSON records as the summary.
    assert json.loads(summary)["marker"] is True
    call = only_call(wire)
    if provider == "claude_code":
        # Read-only: the transcript is in the prompt, and the project stays unchanged.
        tools = call["args"][call["args"].index("--tools") + 1].split(",")
        assert not {"Edit", "Write", "Bash"} & set(tools)
        assert "Edit" not in call["args"] and "Write" not in call["args"]
    if provider == "codex":
        assert "read-only" in call["args"] or any(
            '":workspace_roots"' not in arg and "permissions.raticode.filesystem" in arg
            for arg in call["args"]
        )


# --- Codex's denied files ----------------------------------------------------------------


@pytest.mark.usefixtures("credential_guards")
def test_codex_starts_when_several_home_secret_files_exist(
    wire: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex 0.156 can deny one file; since 11-18 every Codex call denied up to six.

    Its bubblewrap reads empty data for each denied file from one descriptor and closes
    it after the first, so with Claude Code and Codex both signed in every Rem reply,
    voice tidy, planning call and workflow step on Codex failed before it started.
    """
    from gofer.core import credential_mask

    home = Path.home()
    for name in (".claude.json", ".codex/auth.json", ".grok/auth.json", ".netrc"):
        (home / name).parent.mkdir(parents=True, exist_ok=True)
        (home / name).write_text("{}", encoding="utf-8")
    # Bubblewrap may not run inside the test sandbox; without it the other files stay
    # readable, and the profile must still hold one file at most.
    monkeypatch.setattr(credential_mask, "unavailable_reason", lambda *a, **k: "test")
    decision = provider_module.generate_decision(
        "WIRE-MARKER",
        {"provider": "codex", "model": "cli-default"},
        threading.Event(),
        tmp_path / "data",
        system_prompt="WIRE-RULES",
    )
    assert decision == ANSWER
    call = only_call(wire)
    setting = next(a for a in call["args"] if a.startswith("permissions.raticode.filesystem="))
    profile = tomllib.loads(setting.replace("permissions.raticode.filesystem", "profile", 1))
    assert profile["profile"][str(home / ".codex" / "auth.json")] == "deny"
    assert str(home / ".claude.json") not in profile["profile"]
