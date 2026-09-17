from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from gofer.core import provider_capabilities as capabilities
from gofer.core.prompt_envelope import AgentResources
from gofer.core.provider_profiles import (
    ProviderProfile,
    save_provider_profiles,
)
from gofer.rattish.compiler import CompileContext, RattishCompiler
from gofer.rattish.preflight import run_preflight
from gofer.rattish.provider_contracts import load_provider_contracts
from gofer.rattish.provider_runtime import default_provider_subscriptions
from gofer.rattish.runtime import execute_node
from gofer.subscriptions import antigravity
from gofer.ui.chat import _build_chat_command


async def test_native_discovery_preserves_slugs_and_does_not_prompt(monkeypatch):
    commands = []

    async def probe(command, **kwargs):
        commands.append(command)
        if command[-1] == "--version":
            return 0, "1.2.4", ""
        return (
            0,
            (
                "Fetching available models...\n"
                "\x1b[32mgemini-3.8-flash-high\x1b[0m  Gemini 3.8 Flash (High)\n"
                "gemini-3.8-flash-medium\tGemini 3.8 Flash (Medium)\n"
                "claude-sonnet-4-6    Claude Sonnet 4.6 (Thinking)\n"
                "gemini-3.8-flash-high  Duplicate\n"
            ),
            "",
        )

    monkeypatch.setattr(capabilities, "_run_probe", probe)
    result = await capabilities.AdditionalCliCapabilityProbe("antigravity").discover("/bin/agy")
    assert commands == [["/bin/agy", "--version"], ["/bin/agy", "models"]]
    assert result.discovery_status == "ready"
    assert [m.id for m in result.models] == [
        "gemini-3.8-flash-high",
        "claude-sonnet-4-6",
    ]
    assert result.models[0].display_name == "Gemini 3.8 Flash"
    assert [e.id for e in result.models[0].efforts] == ["medium", "high"]
    assert result.models[0].default_effort == "high"
    assert not result.models[1].efforts


@pytest.mark.parametrize(
    ("code", "out", "err", "status"),
    [
        (1, "", "Please sign in to view available models.", "unauthenticated"),
        (0, "Fetching available models...\n", "", "invalid_response"),
        (1, "", "unknown command models", "unsupported_cli_version"),
    ],
)
async def test_discovery_errors(monkeypatch, code, out, err, status):
    async def probe(command, **kwargs):
        return (0, "1.2.4", "") if command[-1] == "--version" else (code, out, err)

    monkeypatch.setattr(capabilities, "_run_probe", probe)
    result = await capabilities.AdditionalCliCapabilityProbe("antigravity").discover("/bin/agy")
    assert result.discovery_status == status
    if status == "unauthenticated":
        assert result.error and "Run agy" in result.error


async def test_catalog_and_runtime_do_not_support_gemini_cli(monkeypatch):
    monkeypatch.setattr(capabilities, "resolve_provider_executable", lambda p: None)
    service = capabilities.ProviderCapabilityService()
    result = await service.payload_async()
    ids = [p["id"] for p in result["providers"]]
    assert "antigravity" in ids and "gemini" not in ids
    assert "gemini" not in service._probes
    assert "antigravity" in default_provider_subscriptions()
    assert "gemini" not in default_provider_subscriptions()


@pytest.fixture
def native_process(tmp_path, monkeypatch):
    script = tmp_path / "fake_agy.py"
    log = tmp_path / "request.json"
    script.write_text(
        "import json,sys\n"
        f"log={str(log)!r}\n"
        "request=json.loads(sys.stdin.readline())\n"
        'open(log,"w").write(json.dumps(request))\n'
        'assert sys.stdin.read()==""\n'
        "records=[\n"
        ' {"event":"init","conversation_id":"one"},\n'
        ' {"event":"step_update","step_update":'
        '{"step_type":"agent_response","text_delta":"Hello"}},\n'
        ' {"event":"step_update","step_update":'
        '{"step_type":"tool","step_index":2,"state":"DONE","tool_name":"read_file"}},\n'
        ' {"event":"step_update","step_update":'
        '{"step_type":"tool","step_index":2,"state":"DONE","tool_name":"read_file"}},\n'
        ' {"event":"result","result":{"status":"SUCCESS","response":"Hello world",'
        '"conversation_id":"one",'
        '"usage":{"input_tokens":12}}}\n'
        "]\n"
        "for record in records:\n"
        ' line=json.dumps(record)+"\\n"\n'
        " for chunk in [line[:9],line[9:]]: sys.stdout.write(chunk);sys.stdout.flush()\n"
    )
    monkeypatch.setattr(
        antigravity, "antigravity_command", lambda *a, **k: [sys.executable, str(script)]
    )
    monkeypatch.setattr(antigravity, "resolve_provider_executable", lambda p: sys.executable)
    return log


async def test_real_subprocess_keeps_reply_out_of_thoughts_and_transmits_exact_prompt(
    native_process, tmp_path
):
    prompt = 'Literal $(text), "quotes", newline\nand unicode ★'
    events = [
        e
        async for e in antigravity.stream_antigravity(
            prompt, cwd=tmp_path, permission_mode="cli-managed"
        )
    ]
    assert json.loads(native_process.read_text())["message"]["content"] == prompt
    assert [e["text"] for e in events if e["type"] == "thought"] == ["read file"]
    assert events[-1]["message"]["body"] == "Hello world"
    assert events[-1]["sessionId"] == "one"
    assert events[-1]["type"] == "final"
    assert events[-1]["usage"] == {"input_tokens": 12}


@pytest.mark.parametrize("mode", [None, "default", "danger-full-access"])
def test_permissions_do_not_silently_elevate(mode):
    with pytest.raises(ValueError):
        antigravity.antigravity_command("/bin/agy", permission_mode=mode)


def test_rem_uses_native_protocol_and_exact_model():
    command = _build_chat_command(
        "antigravity",
        "gemini-3.8-flash-high",
        "hello",
        binary_path="/bin/agy",
        permission_mode="cli-managed",
    )
    assert command == [
        "/bin/agy",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--model",
        "gemini-3.8-flash-high",
    ]
    assert "--dangerously-skip-permissions" not in command
    assert "--acp" not in command


def test_mcp_staging_does_not_modify_workspace_or_home(tmp_path):
    resources = AgentResources.model_validate(
        {
            "mcpServers": [
                {"name": "docs", "type": "http", "url": "https://example.com/mcp", "enabled": True},
                {
                    "name": "local",
                    "type": "stdio",
                    "command": "/bin/tool",
                    "args": ["serve"],
                    "enabled": True,
                },
            ]
        }
    )
    existing = tmp_path / ".agents" / "mcp_config.json"
    existing.parent.mkdir()
    existing.write_text("preserve this byte for byte")
    with antigravity.antigravity_workspace(tmp_path, resources) as (cwd, args):
        assert cwd != tmp_path
        assert args == ["--add-dir", str(tmp_path)]
        config = json.loads((cwd / ".agents/mcp_config.json").read_text())
        assert list(config["mcpServers"]) == ["docs", "local"]
        assert list(config["mcpServers"].values()) == [
            {"serverUrl": "https://example.com/mcp"},
            {"command": "/bin/tool", "args": ["serve"], "cwd": str(tmp_path)},
        ]
    assert not cwd.exists()
    assert existing.read_text() == "preserve this byte for byte"


async def test_agent_contract_preflight_and_execution(native_process, tmp_path):
    root = Path(__file__).parents[2] / "rattish"
    compiler = RattishCompiler.from_paths(
        schema_root=root / "schemas", contract_paths=[root / "contracts/agent.json"]
    )
    providers = load_provider_contracts(
        root / "schemas/provider-contract.schema.json", (root / "providers").glob("*.json")
    )
    ir = compiler.compile(
        "Rattish: 1\nWorkflow:\n  name: Native\nNode run:\n  type: agent\n"
        "  provider: antigravity\n  profile: native\n  model: gemini-3.8-flash-high\n"
        "  prompt: Exact goal\n",
        CompileContext("native", tmp_path, provider_contracts=providers),
    ).ir
    data = tmp_path / "data"
    save_provider_profiles(
        {"native": ProviderProfile(name="native", subscription="antigravity")}, data_dir=data
    )
    subscriptions = default_provider_subscriptions()
    assert not run_preflight(ir, subscriptions=subscriptions, data_dir=data).ready
    save_provider_profiles(
        {
            "native": ProviderProfile(
                name="native", subscription="antigravity", approval_mode="cli-managed"
            )
        },
        data_dir=data,
    )
    assert run_preflight(ir, subscriptions=subscriptions, data_dir=data).ready
    result = await execute_node(ir, "run", subscriptions=subscriptions, data_dir=data)
    assert result.outcome == "success", result
    assert "Exact goal" in native_process.read_text()


@pytest.mark.parametrize(
    ("records", "exitcode"),
    [
        (
            [
                {
                    "event": "result",
                    "result": {"status": "ERROR", "response": "", "error": "Sign in"},
                }
            ],
            0,
        ),
        ([{"event": "init"}], 0),
        ([{"event": "result", "result": {"status": "SUCCESS", "response": "Done"}}], 3),
        ([{"event": "result", "result": {"status": "INTERRUPTED", "response": "Partial"}}], 0),
        (["bad json"], 0),
    ],
)
async def test_failure_never_becomes_success(monkeypatch, tmp_path, records, exitcode):
    async def source(*args, **kwargs):
        for record in records:
            yield {
                "type": "chunk",
                "stream": "stdout",
                "text": json.dumps(record) + "\n",
                "returncode": None,
            }
        yield {"type": "exit", "stream": None, "text": "", "returncode": exitcode}

    monkeypatch.setattr(antigravity, "stream_subprocess", source)
    events = [
        e
        async for e in antigravity.stream_antigravity(
            "hello", cwd=tmp_path, executable="/bin/agy", permission_mode="cli-managed"
        )
    ]
    assert events[-1]["type"] == "error"
    assert events[-1]["exitCode"] != 0


async def test_cancellation_and_timeout_drain_native_process(monkeypatch, tmp_path):
    import threading

    script = tmp_path / "sleep.py"
    script.write_text("import sys,time\nsys.stdin.read()\ntime.sleep(30)\n")
    monkeypatch.setattr(
        antigravity, "antigravity_command", lambda *a, **k: [sys.executable, str(script)]
    )
    cancel = threading.Event()
    cancel.set()
    for kwargs in [{"timeout": 0.05}, {"cancel_event": cancel}]:
        events = [e async for e in antigravity.stream_antigravity("test", cwd=tmp_path, **kwargs)]
        assert events[-1]["type"] == "error"
        assert events[-1]["exitCode"] != 0


def test_antigravity_short_native_slug_and_default_sentinel():
    from gofer.core.planner import _provider_binary

    assert _provider_binary("antigravity") == "agy"
    models = capabilities._antigravity_models_from_listing("auto  Auto\n")
    assert models[0].id == "auto"
    capabilities._validate_capability_selection(
        capabilities.ProviderCapability(
            id="antigravity",
            display_name="Antigravity",
            available=True,
            discovery_status="ready",
            models=models,
            supports_custom_model=True,
        ),
        "cli-default",
        "cli-default",
    )


async def test_rem_routes_existing_thread_to_native_antigravity(monkeypatch, tmp_path):
    from gofer.ui import chat

    captured: dict[str, Any] = {}
    monkeypatch.setattr(chat, "resolve_provider_executable", lambda p: "/fake/agy")
    monkeypatch.setattr(chat, "ensure_local_gofer_cli", lambda p: Path("/fake/gof"))
    monkeypatch.setattr(chat, "provider_preference", lambda p: {})

    async def native(prompt, **kwargs):
        captured.update(kwargs, prompt=prompt)
        yield {
            "type": "final",
            "error": None,
            "message": {"role": "assistant", "body": "Ready"},
            "exitCode": 0,
        }

    monkeypatch.setattr(chat, "stream_antigravity", native)
    result = await chat.run_workflow_chat(
        "antigravity",
        "cli-default",
        [
            {"role": "user", "body": "Keep my project context"},
            {"role": "assistant", "body": "Earlier answer"},
            {"role": "user", "body": "Continue after switching provider"},
        ],
        None,
        working_dir=tmp_path,
        data_dir=tmp_path / "data",
        permission_mode="cli-managed",
    )
    assert result["message"]["body"] == "Ready"
    assert result["provider"] == "antigravity"
    assert captured["cwd"] == tmp_path
    assert captured["permission_mode"] == "cli-managed"
    assert "Rem" in captured["prompt"]
    assert "Earlier answer" in captured["prompt"]
    assert "Continue after switching provider" in captured["prompt"]


@pytest.mark.parametrize("effort", ["low", "medium", "high"])
def test_grouped_effort_resolves_exact_native_id(effort):
    command = antigravity.antigravity_command(
        "/bin/agy",
        model="gemini-3.8-flash-high",
        effort=effort,
        permission_mode="cli-managed",
    )
    assert command[command.index("--model") + 1] == f"gemini-3.8-flash-{effort}"
    assert "--effort" not in command


def test_legacy_native_effort_selection_is_valid():
    catalog = capabilities._antigravity_models_from_listing(
        "gemini-3.1-pro-high  Gemini 3.1 Pro (High)\n"
        "gemini-3.1-pro-low  Gemini 3.1 Pro (Low)\n"
        "gpt-oss-120b-medium  GPT-OSS 120B (Medium)\n"
    )
    assert [e.id for e in catalog[0].efforts] == ["low", "high"]
    assert [e.id for e in catalog[1].efforts] == ["medium"]
    provider = capabilities.ProviderCapability(
        id="antigravity",
        display_name="Antigravity",
        available=True,
        discovery_status="ready",
        models=catalog,
    )
    capabilities._validate_capability_selection(provider, "gemini-3.1-pro-low", None)
    with pytest.raises(capabilities.ProviderCapabilityError):
        capabilities._validate_capability_selection(provider, "gemini-3.1-pro-high", "medium")
