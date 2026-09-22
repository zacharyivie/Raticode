"""Synthetic fixtures from Cursor docs and OpenCode v1.18.31, not live captures."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from gofer.core.prompt_envelope import AgentResources
from gofer.core.provider_capabilities import AdditionalCliCapabilityProbe, _is_cursor_agent
from gofer.core.provider_profiles import (
    ProviderProfile,
    ResolvedProviderSettings,
    validate_provider_settings,
)
from gofer.rattish.provider_runtime import default_provider_subscriptions, runtime_subscription_id
from gofer.subscriptions.cli_providers import (
    CliOutput,
    CliSubscription,
    cli_command,
    cli_invocation,
    stream_cli,
)
from gofer.ui.chat import ChatProviderError, _build_chat_command


def wire(*records: dict[str, Any]) -> str:
    return "".join(json.dumps(record) + "\n" for record in records)


@pytest.mark.parametrize("width", [1, 7, 10000])
def test_cursor_segments_do_not_repeat_terminal_aggregate(width):
    stdout = wire(
        {"type": "system", "session_id": "cursor-session"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello "}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "world"}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": "Hello world"},
    )
    result = CliOutput("cursor")
    emitted = []
    for offset in range(0, len(stdout), width):
        emitted.extend(result.feed(stdout[offset : offset + width]))
    result.finish(0, "")
    assert result.completed and result.text == "Hello world"
    assert "".join(emitted) == result.text
    assert result.session_id == "cursor-session"


def test_cursor_result_only_and_missing_result():
    result = CliOutput("cursor")
    result.feed(wire({"type": "result", "subtype": "success", "result": "Done"}))
    result.finish(0, "")
    assert result.text == "Done" and result.completed
    missing = CliOutput("cursor")
    missing.feed(wire({"type": "system", "session_id": "id"}))
    missing.finish(0, "")
    assert "without a successful result" in (missing.error or "")


def test_opencode_step_is_not_completion_and_duplicate_parts_are_ignored():
    result = CliOutput("opencode")
    result.feed(
        wire({"type": "step_finish", "sessionID": "oc-session", "part": {"reason": "tool-calls"}})
    )
    assert not result.completed
    text = {"type": "text", "sessionID": "oc-session", "part": {"id": "p1", "text": "Done"}}
    result.feed(wire(text, text, {"type": "step_finish", "part": {"reason": "stop"}}))
    result.finish(0, "")
    assert result.text == "Done" and result.completed and result.session_id == "oc-session"


@pytest.mark.parametrize("provider", ["cursor", "opencode"])
@pytest.mark.parametrize("bad", ["{truncated", "[]", "noise\n"])
def test_malformed_wire_is_failure_even_with_zero_exit(provider, bad):
    result = CliOutput(provider)
    result.feed(bad)
    result.finish(0, "")
    assert result.error and not result.completed


def test_error_record_zero_exit_and_session_switch_are_failures():
    result = CliOutput("opencode")
    result.feed(
        wire(
            {"type": "error", "error": {"name": "AuthError", "data": {"message": "Login required"}}}
        )
    )
    result.finish(0, "")
    assert result.error == "Login required"
    result = CliOutput("opencode")
    result.feed(
        wire({"type": "step_start", "sessionID": "one"}, {"type": "step_start", "sessionID": "two"})
    )
    result.finish(0, "")
    assert "session identity" in (result.error or "")


@pytest.mark.parametrize("provider", ["cursor", "copilot", "opencode"])
def test_profiles_runtime_and_exact_multiline_prompt(provider, tmp_path):
    prompt = "Quotes ' \" and\n$(never run this)"
    profile = ProviderProfile(name="custom", subscription=provider, model="vendor/custom")
    assert ProviderProfile.model_validate_json(profile.model_dump_json()).model == "vendor/custom"
    settings = ResolvedProviderSettings(
        subscription=provider, model=profile.model, effort="cli-default"
    )
    validate_provider_settings(settings)
    assert runtime_subscription_id(provider) == provider
    adapter = default_provider_subscriptions()[provider]
    command = adapter._build_command(prompt, [], [], provider_settings=settings)
    assert command[command.index("--model") + 1] == "vendor/custom"
    assert command.count(prompt) == 1
    assert "--resume" not in command and "--session" not in command
    if provider != "copilot":
        with pytest.raises(ValueError, match="effort"):
            validate_provider_settings(
                ResolvedProviderSettings(subscription=provider, effort="high")
            )
    with pytest.raises(ValueError, match="legacy"):
        adapter._build_command(prompt, ["unmapped-tool"], [])


@pytest.mark.parametrize("provider", ["cursor", "copilot", "opencode"])
def test_rem_unsupported_options_fail_instead_of_disappearing(provider, tmp_path):
    if provider not in {"cursor", "copilot"}:
        with pytest.raises(ChatProviderError, match="effort"):
            _build_chat_command(provider, "custom", "prompt", effort="high")
    with pytest.raises(ChatProviderError, match="image"):
        _build_chat_command(provider, "custom", "prompt", image_paths=[tmp_path / "img.png"])
    with pytest.raises(ValueError, match="permission"):
        _build_chat_command(provider, "custom", "prompt", permission_mode="danger-full-access")


@pytest.mark.parametrize("effort", ["low", "low-fast", "medium", "medium-fast", "extra-high"])
def test_cursor_effort_reaches_rem_and_subscription_commands(effort):
    settings = ResolvedProviderSettings(
        subscription="cursor", model="cursor-grok-4.5", effort=effort
    )
    validate_provider_settings(settings)
    adapter = default_provider_subscriptions()["cursor"]
    commands = [
        adapter._build_command("prompt", [], [], provider_settings=settings),
        _build_chat_command("cursor", settings.model, "prompt", effort=effort),
    ]
    for command in commands:
        assert command[command.index("--model") + 1] == f"cursor-grok-4.5-{effort}"
        assert "--effort" not in command
        assert "--trust" in command
        assert "--yolo" not in command and "--force" not in command


def swarm_resources(url: str = "http://localhost:1234/tool", **kwargs: Any) -> AgentResources:
    return AgentResources.model_validate({"mcpServers": [{"name": "swarm", "url": url}], **kwargs})


@pytest.mark.parametrize("provider", ["copilot", "opencode"])
def test_trusted_endpoint_mismatch_rejects_before_spawn(provider):
    with pytest.raises(ValueError, match="trusted"):
        with cli_invocation(
            provider, [provider], swarm_resources(), trusted_swarm_url="http://localhost:9876/other"
        ):
            pytest.fail("Untrusted endpoint was granted")


def test_copilot_concurrent_configs_cleanup_and_denials():
    first_url, second_url = "http://localhost:1234/first", "http://localhost:1234/second"
    with cli_invocation(
        "copilot", ["copilot"], swarm_resources(first_url, shell=False), trusted_swarm_url=first_url
    ) as (first, _):
        path1 = Path(first[first.index("--additional-mcp-config") + 1][1:])
        first_config = json.loads(path1.read_text())
        alias1 = next(iter(first_config["mcpServers"]))
        assert first_config["mcpServers"][alias1] == {
            "url": first_url,
            "type": "http",
            "tools": ["swarm_action"],
        }
        assert f"{alias1}(swarm_action)" in first
        assert "shell" in [first[i + 1] for i, value in enumerate(first) if value == "--deny-tool"]
        with cli_invocation(
            "copilot", ["copilot"], swarm_resources(second_url), trusted_swarm_url=second_url
        ) as (second, _):
            path2 = Path(second[second.index("--additional-mcp-config") + 1][1:])
            assert path2 != path1 and path2.exists()
            assert alias1 not in json.loads(path2.read_text())["mcpServers"]
        assert not path2.exists() and path1.exists()
    assert not path1.exists()


def test_opencode_config_preserves_auth_environment_and_exact_tool_grants(monkeypatch):
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", json.dumps({"theme": "custom"}))
    monkeypatch.setenv("EXAMPLE_AUTH_TOKEN", "test-value")
    url = "http://localhost:1234/turn"
    with cli_invocation(
        "opencode", ["opencode"], swarm_resources(url, shell=False), trusted_swarm_url=url
    ) as (_, env):
        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        assert config["theme"] == "custom"
        assert "EXAMPLE_AUTH_TOKEN" not in env  # process utility inherits original environment
        alias = next(iter(config["mcp"]))
        assert config["mcp"][alias] == {"type": "remote", "url": url, "oauth": False}
        permissions = json.loads(env["OPENCODE_PERMISSION"])
        assert permissions["*"] == permissions["bash"] == permissions["webfetch"] == "deny"
        assert permissions[alias + "_swarm_action"] == "allow"
        assert alias + "_*" not in permissions


def test_cursor_plugin_contains_only_owned_config_and_cleans_on_failure():
    url = "http://localhost:1234/turn"
    with pytest.raises(RuntimeError):
        with cli_invocation(
            "cursor", ["cursor-agent"], swarm_resources(url, web=True), trusted_swarm_url=url
        ) as (command, _):
            path = Path(command[command.index("--plugin-dir") + 1])
            manifest = json.loads((path / ".cursor-plugin/plugin.json").read_text())
            config = json.loads((path / ".mcp.json").read_text())
            assert manifest["name"].startswith("raticode-")
            assert next(iter(config["mcpServers"].values())) == {
                "url": url,
                "enabledTools": ["swarm_action"],
            }
            raise RuntimeError("spawn failed")
    assert not path.exists()
    with cli_invocation("cursor", ["cursor-agent"], AgentResources(shell=False)) as (command, env):
        assert "--disable-project-configs" in command
        native = command[command.index("--allowed-tools") + 1]
        assert "shell_tool_call" not in native and "fetch_tool_call" not in native
        assert "get_mcp_tools_tool_call" in native.split(",")
        config = json.loads((Path(env["CURSOR_CONFIG_DIR"]) / "cli-config.json").read_text())
        assert config["permissions"]["deny"] == ["Shell(*)", "WebFetch(*)"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["cursor", "copilot", "opencode"])
async def test_adapter_cancellation_and_nonzero_exit_keep_partial_text(
    provider, monkeypatch, tmp_path
):
    cancelled = threading.Event()

    async def fake(*args, **kwargs):
        content = (
            "partial"
            if provider == "copilot"
            else wire(
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "partial"}]}}
            )
            if provider == "cursor"
            else wire({"type": "text", "part": {"text": "partial"}})
        )
        yield {"type": "chunk", "stream": "stdout", "text": content}
        cancelled.set()
        yield {"type": "exit", "stream": None, "returncode": 130}

    async def check(*args):
        return None

    monkeypatch.setattr("gofer.subscriptions.cli_providers.check_cursor_plugins", check)
    monkeypatch.setattr("gofer.subscriptions.cli_providers.stream_subprocess", fake)
    result = await CliSubscription(provider).execute(
        "real prompt", tmp_path, [], [], {}, cancel_event=cancelled
    )
    assert not result.success and result.output == "partial" and result.exit_code == 130


@pytest.mark.asyncio
async def test_stream_never_succeeds_on_auth_failure(monkeypatch, tmp_path):
    async def fake(*args, **kwargs):
        yield {"type": "chunk", "stream": "stderr", "text": "Authentication required"}
        yield {"type": "exit", "stream": None, "returncode": 1}

    monkeypatch.setattr("gofer.subscriptions.cli_providers.stream_subprocess", fake)
    events = [event async for event in stream_cli(["copilot"], "copilot", cwd=tmp_path, env={})]
    assert events[-1]["type"] == "error" and events[-1]["error"] == "Authentication required"


def test_generic_agent_must_identify_as_cursor(monkeypatch):
    class Help:
        returncode = 0
        stdout = "Unrelated agent utility"

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: Help())
    assert not _is_cursor_agent("/example/agent")
    Help.stdout = "Start the Cursor Agent"
    assert _is_cursor_agent("/example/agent")


@pytest.mark.asyncio
async def test_opencode_models_are_full_ids(monkeypatch):
    async def probe(command):
        return 0, "openai/custom-model\nanthropic/other\nnot a model", ""

    monkeypatch.setattr("gofer.core.provider_capabilities._run_probe", probe)
    result = await AdditionalCliCapabilityProbe("opencode")._discover_available(
        "opencode", "1.18.31"
    )
    assert [model.id for model in result.models] == ["openai/custom-model", "anthropic/other"]
    assert result.supports_custom_model


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["cursor", "copilot", "opencode"])
async def test_rattish_compile_preflight_runtime_selects_actual_adapter(
    provider, tmp_path, monkeypatch
):
    from gofer.rattish.compiler import CompileContext, RattishCompiler
    from gofer.rattish.preflight import run_preflight
    from gofer.rattish.provider_contracts import load_provider_contracts
    from gofer.rattish.runtime import execute_node

    root = Path(__file__).parents[2] / "rattish"
    compiler = RattishCompiler.from_paths(
        schema_root=root / "schemas", contract_paths=[root / "contracts/agent.json"]
    )
    providers = load_provider_contracts(
        root / "schemas/provider-contract.schema.json", (root / "providers").glob("*.json")
    )
    source = (
        "Rattish: 1\nWorkflow:\n  name: CLI test\nNode run:\n  type: agent\n"
        f"  provider: {provider}\n  model: vendor/custom\n  prompt: Preserve this exact goal\n"
    )
    ir = compiler.compile(
        source, CompileContext("cli-test", tmp_path, provider_contracts=providers)
    ).ir
    seen = []

    async def fake(command, **kwargs):
        seen.append(command)
        output = (
            "Done"
            if provider == "copilot"
            else wire({"type": "result", "subtype": "success", "result": "Done"})
            if provider == "cursor"
            else wire({"type": "text", "part": {"text": "Done"}})
        )
        yield {"type": "chunk", "stream": "stdout", "text": output}
        yield {"type": "exit", "stream": None, "returncode": 0}

    async def check(*args):
        pass

    monkeypatch.setattr("gofer.subscriptions.cli_providers.stream_subprocess", fake)
    monkeypatch.setattr("gofer.subscriptions.cli_providers.check_cursor_plugins", check)
    monkeypatch.setattr(
        "gofer.subscriptions.cli_providers.resolve_provider_executable", lambda p: p
    )
    subscriptions = {provider: CliSubscription(provider)}
    report = run_preflight(ir, subscriptions=subscriptions, data_dir=tmp_path / "data")
    assert report.ready, report
    result = await execute_node(ir, "run", subscriptions=subscriptions, data_dir=tmp_path / "data")
    assert result.outcome == "success", result
    assert seen[0][seen[0].index("--model") + 1] == "vendor/custom"
    assert any("Preserve this exact goal" in arg for arg in seen[0])
    monkeypatch.setattr(
        "gofer.subscriptions.cli_providers.resolve_provider_executable", lambda p: None
    )
    assert not run_preflight(ir, subscriptions=subscriptions, data_dir=tmp_path / "data").ready


@pytest.mark.parametrize("provider", ["cursor", "copilot", "opencode"])
def test_swarm_roster_persists_provider_and_model(provider, tmp_path):
    from gofer.ui.swarms import SwarmManager

    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    try:
        swarm = manager.create(
            tmp_path,
            {
                "name": "Provider team",
                "agents": [
                    {
                        "id": "lead",
                        "name": "Lead",
                        "role": "Coordinate",
                        "provider": provider,
                        "model": "vendor/custom",
                        "isOrchestrator": True,
                    }
                ],
            },
        )
        assert swarm["agents"][0]["provider"] == provider
        assert swarm["agents"][0]["model"] == "vendor/custom"
    finally:
        manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "version",
    ["2026.09.09", "2026.09.18-9a7762b", "2099.01.01-new", "1.0", ""],
)
async def test_cursor_resource_check_does_not_gate_builds(version, monkeypatch):
    from gofer.subscriptions.cli_providers import check_cursor_plugins

    commands = []

    async def probe(command, **kwargs):
        commands.append(command)
        return (
            0,
            version if command[-1] == "--version" else "Start the Cursor Agent --plugin-dir",
            "",
        )

    monkeypatch.setattr("gofer.subscriptions.cli_providers.run_subprocess", probe)
    monkeypatch.delenv("CURSOR_ENABLE_BEDROCK", raising=False)
    monkeypatch.delenv("CURSOR_LOCAL_AGENT_BASE_URL", raising=False)
    await check_cursor_plugins("cursor-agent")
    assert commands == [["cursor-agent", "--help"]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "help_code, help_text, env_name, message",
    [
        (1, "Start the Cursor Agent --plugin-dir", None, "must support --plugin-dir"),
        (0, "Start the Cursor Agent", None, "must support --plugin-dir"),
        (0, "Other CLI --plugin-dir", None, "must support --plugin-dir"),
        (0, "Start the Cursor Agent --plugin-dir", "CURSOR_ENABLE_BEDROCK", "normal mode"),
        (0, "Start the Cursor Agent --plugin-dir", "CURSOR_LOCAL_AGENT_BASE_URL", "normal mode"),
    ],
)
async def test_cursor_resource_check_preserves_capability_guards(
    help_code, help_text, env_name, message, monkeypatch
):
    from gofer.subscriptions.cli_providers import check_cursor_plugins

    async def probe(command, **kwargs):
        return help_code, help_text, ""

    monkeypatch.setattr("gofer.subscriptions.cli_providers.run_subprocess", probe)
    monkeypatch.delenv("CURSOR_ENABLE_BEDROCK", raising=False)
    monkeypatch.delenv("CURSOR_LOCAL_AGENT_BASE_URL", raising=False)
    if env_name:
        monkeypatch.setenv(env_name, "1")
    with pytest.raises(ValueError, match=message):
        await check_cursor_plugins("cursor-agent")


def test_catalog_publishes_default_only_permissions_for_new_providers():
    from gofer.core.provider_capabilities import ProviderCapability

    for provider in ("cursor", "copilot", "opencode"):
        payload = ProviderCapability(
            id=provider, display_name=provider, available=False, discovery_status="missing"
        ).to_ui_payload()
        assert payload["permissionModes"] == [{"id": "default", "displayName": "CLI default"}]
        assert payload["defaultPermissionMode"] == "default"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["cursor", "copilot", "opencode"])
@pytest.mark.parametrize("scope", ["project", "global"])
async def test_rem_stream_and_nonstream_preserve_context_and_cleanup(
    provider, scope, tmp_path, monkeypatch
):
    from gofer.ui import chat

    working_dir = tmp_path if scope == "project" else None
    expected_cwd = tmp_path if scope == "project" else tmp_path / "data"
    expected_cwd.mkdir(exist_ok=True)
    seen = []
    paths = []

    async def check(*args, **kwargs):
        pass

    async def fake(command, **kwargs):
        seen.append(command)
        if provider == "cursor":
            assert "--trust" in command
            assert kwargs["cwd"] == expected_cwd
            path = Path(command[command.index("--plugin-dir") + 1])
            assert path.exists()
            paths.append(path)
            stdout = wire(
                {
                    "type": "result",
                    "subtype": "success",
                    "session_id": "cursor-only",
                    "result": "Answer",
                }
            )
        elif provider == "copilot":
            path = Path(command[command.index("--additional-mcp-config") + 1][1:])
            assert path.exists()
            paths.append(path)
            stdout = "Answer"
        else:
            config = json.loads(kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
            assert "mcp" in config
            stdout = wire(
                {"type": "text", "sessionID": "opencode-only", "part": {"text": "Answer"}}
            )
        yield {"type": "chunk", "stream": "stdout", "text": stdout[:3]}
        yield {"type": "chunk", "stream": "stdout", "text": stdout[3:]}
        yield {"type": "exit", "stream": None, "returncode": 0}

    async def probe(command, **kwargs):
        return (
            0,
            "2026.09.15-d2fe57e"
            if command[-1] == "--version"
            else "Start the Cursor Agent --plugin-dir",
            "",
        )

    monkeypatch.setattr("gofer.subscriptions.cli_providers.run_subprocess", probe)
    monkeypatch.delenv("CURSOR_ENABLE_BEDROCK", raising=False)
    monkeypatch.delenv("CURSOR_LOCAL_AGENT_BASE_URL", raising=False)
    monkeypatch.setattr(chat, "validate_provider_selection_async", check)
    monkeypatch.setattr(chat, "resolve_provider_executable", lambda p: p)
    monkeypatch.setattr(chat, "ensure_local_gofer_cli", lambda data: tmp_path / "gof")
    monkeypatch.setattr("gofer.subscriptions.cli_providers.stream_subprocess", fake)
    messages = [
        {"role": "user", "body": "Remember this earlier goal"},
        {"role": "assistant", "body": "Existing progress"},
        {"role": "user", "body": "Now continue"},
    ]
    events = [
        event
        async for event in chat.stream_workflow_chat(
            provider,
            "custom/model",
            messages,
            None,
            working_dir=working_dir,
            data_dir=tmp_path / "data",
            agent_instructions="Keep this persona",
        )
    ]
    assert events[-1]["type"] == "final" and events[-1]["message"]["body"] == "Answer"
    assert events[-1]["sessionId"] == (None if provider == "copilot" else provider + "-only")
    assert all(not path.exists() for path in paths)
    prompt = next(arg for arg in seen[-1] if "Remember this earlier goal" in arg)
    assert (
        "Existing progress" in prompt and "Now continue" in prompt and "Keep this persona" in prompt
    )
    assert "--resume" not in seen[-1] and "--session" not in seen[-1]
    result = await chat.run_workflow_chat(
        provider,
        "custom/model",
        messages,
        None,
        working_dir=working_dir,
        data_dir=tmp_path / "data",
    )
    assert result["message"]["body"] == "Answer"
    assert all(not path.exists() for path in paths)


def test_all_workflow_entrypoints_register_the_new_adapters():
    from gofer.cli.commands import agent, workflow
    from gofer.core import runner, watcher
    from gofer.ui import api

    for registrations in (
        agent._SUBSCRIPTIONS,
        workflow._SUBSCRIPTIONS,
        runner._SUBSCRIPTIONS,
        watcher._subscriptions,
        api._subscriptions,
    ):
        for provider in ("cursor", "copilot", "opencode"):
            assert isinstance(registrations[provider], CliSubscription)
            assert registrations[provider].provider == provider


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_copilot_effort_reaches_rem_and_subscription(effort):
    settings = ResolvedProviderSettings(subscription="copilot", model="gpt-test", effort=effort)
    validate_provider_settings(settings)
    adapter = default_provider_subscriptions()["copilot"]
    for command in (
        adapter._build_command("prompt", [], [], provider_settings=settings),
        _build_chat_command("copilot", settings.model, "prompt", effort=effort),
    ):
        assert command[command.index("--reasoning-effort") + 1] == effort
        assert command[command.index("--model") + 1] == "gpt-test"


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_code", [0, 1])
async def test_copilot_reply_chunks_are_not_thoughts(monkeypatch, tmp_path, exit_code):
    chunks = ["Hi", "! I'm Rem. I can help", " you build,", "\nedit workflows."]

    async def fake(command, **kwargs):
        assert command[command.index("--add-dir") + 1] == str(tmp_path.resolve())
        assert "--allow-all-tools" not in command
        assert "--allow-all-paths" not in command
        assert "--silent" in command
        for chunk in chunks:
            yield {"type": "chunk", "stream": "stdout", "text": chunk}
        yield {"type": "exit", "stream": None, "returncode": exit_code}

    monkeypatch.setattr("gofer.subscriptions.cli_providers.stream_subprocess", fake)
    events = [
        event
        async for event in stream_cli(
            cli_command("copilot", "hi", executable="copilot"),
            "copilot",
            cwd=tmp_path,
            env={},
        )
    ]
    assert len(events) == 1
    assert events[0]["type"] == ("final" if exit_code == 0 else "error")
    assert events[0]["message"]["body"] == "".join(chunks)
