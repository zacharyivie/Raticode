"""Provider wiring with synthetic ACP subprocesses; no authentication/model calls."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from gofer.core.provider_profiles import ProviderProfile, save_provider_profiles
from gofer.rattish.compiler import CompileContext, RattishCompiler
from gofer.rattish.preflight import run_preflight
from gofer.rattish.provider_contracts import load_provider_contracts
from gofer.rattish.provider_runtime import default_provider_subscriptions
from gofer.rattish.runtime import execute_node
from gofer.subscriptions.acp_providers import acp_command, stream_acp
from gofer.ui.swarms import SwarmManager


@pytest.fixture
def protocol(tmp_path, monkeypatch):
    script = tmp_path / "provider.py"
    log = tmp_path / "requests.jsonl"
    script.write_text(
        "import json,sys,time\n"
        f"log = open({str(log)!r}, 'a')\n"
        "for line in sys.stdin:\n"
        " request=json.loads(line); log.write(line); log.flush()\n"
        " method=request['method']\n"
        " if method=='initialize': result={'protocolVersion':1,'agentCapabilities':{}}\n"
        " elif method=='session/new': result={'sessionId':'fresh'}\n"
        " elif method=='session/set_model': result={}\n"
        " elif method=='session/prompt':\n"
        "  print(json.dumps({'jsonrpc':'2.0','method':'session/update',"
        "'params':{'sessionId':'fresh',"
        "'update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'Done'}}}}),flush=True)\n"
        "  result={'stopReason':'end_turn'}\n"
        " else: raise RuntimeError(method)\n"
        " print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}),flush=True)\n"
    )
    monkeypatch.setattr(
        "gofer.subscriptions.acp_providers.acp_command", lambda *a: [sys.executable, str(script)]
    )
    monkeypatch.setattr(
        "gofer.subscriptions.acp_providers.resolve_provider_executable", lambda p: sys.executable
    )

    async def identity(*args):
        return "test-version"

    monkeypatch.setattr("gofer.subscriptions.acp_providers.grok_build_version", identity)
    return log


@pytest.mark.parametrize("provider", ["grok"])
async def test_explicit_native_mode_preserves_prompt_model_and_fresh_session(
    provider, protocol, tmp_path
):
    prompt = "Preserve quotes ' \" and\n$(literal text)"
    for _ in range(2):
        events = [
            event
            async for event in stream_acp(
                provider,
                prompt,
                cwd=tmp_path,
                model="custom/model",
                permission_mode="cli-managed",
            )
        ]
        assert events[-1]["type"] == "final"
        assert events[-1]["message"]["body"] == "Done"
    requests = [json.loads(line) for line in protocol.read_text().splitlines()]
    assert [r["method"] for r in requests].count("session/new") == 2
    assert all(
        r["params"]["prompt"] == [{"type": "text", "text": prompt}]
        for r in requests
        if r["method"] == "session/prompt"
    )
    assert all(
        r["params"]["modelId"] == "custom/model"
        for r in requests
        if r["method"] == "session/set_model"
    )
    for r in requests:
        if r["method"] == "session/new" and "_meta" in r["params"]:
            assert not Path(r["params"]["_meta"]["pluginDirs"][0]).exists()


@pytest.mark.parametrize("provider", ["grok"])
async def test_strict_mode_never_spawns_or_silently_elevates(provider, protocol, tmp_path):
    with pytest.raises(ValueError, match="cannot enforce"):
        _ = [e async for e in stream_acp(provider, "Do work", cwd=tmp_path)]
    assert not protocol.exists()


@pytest.mark.parametrize("provider", ["grok"])
async def test_agent_compile_preflight_and_runtime_require_native_profile(
    provider, protocol, tmp_path
):
    root = Path(__file__).parents[2] / "rattish"
    compiler = RattishCompiler.from_paths(
        schema_root=root / "schemas", contract_paths=[root / "contracts/agent.json"]
    )
    providers = load_provider_contracts(
        root / "schemas/provider-contract.schema.json", (root / "providers").glob("*.json")
    )
    source = (
        "Rattish: 1\nWorkflow:\n  name: ACP test\nNode run:\n  type: agent\n"
        f"  provider: {provider}\n  profile: native\n  model: custom/model\n  prompt: Exact goal\n"
    )
    ir = compiler.compile(
        source, CompileContext("acp-test", tmp_path, provider_contracts=providers)
    ).ir
    data = tmp_path / "data"
    save_provider_profiles(
        {"native": ProviderProfile(name="native", subscription=provider)}, data_dir=data
    )
    subscriptions = default_provider_subscriptions()
    assert not run_preflight(ir, subscriptions=subscriptions, data_dir=data).ready
    save_provider_profiles(
        {
            "native": ProviderProfile(
                name="native", subscription=provider, approval_mode="cli-managed"
            )
        },
        data_dir=data,
    )
    report = run_preflight(ir, subscriptions=subscriptions, data_dir=data)
    assert report.ready, report
    result = await execute_node(ir, "run", subscriptions=subscriptions, data_dir=data)
    assert result.outcome == "success", result
    assert "Exact goal" in protocol.read_text()


@pytest.mark.parametrize("provider", ["grok"])
def test_swarm_persists_explicit_permission_mode(provider, tmp_path):
    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    try:
        swarm = manager.create(
            tmp_path,
            {
                "name": "Provider",
                "agents": [
                    {
                        "name": "Lead",
                        "role": "Coordinate",
                        "provider": provider,
                        "permissionMode": "cli-managed",
                        "isOrchestrator": True,
                    }
                ],
            },
        )
        assert swarm["agents"][0]["permissionMode"] == "cli-managed"
        assert swarm["agents"][0]["provider"] == provider
    finally:
        manager.close()


def test_acp_argv_uses_provider_protocol_and_no_session_reuse():
    with pytest.raises(ValueError, match="Unknown ACP provider"):
        acp_command("gemini", "/bin/gemini")
    assert acp_command("grok", "/bin/grok") == ["/bin/grok", "agent", "--no-leader", "stdio"]
    assert acp_command("grok", "/bin/grok", "/tmp/plugin with spaces") == [
        "/bin/grok",
        "agent",
        "--no-leader",
        "--plugin-dir",
        "/tmp/plugin with spaces",
        "stdio",
    ]


async def test_unlimited_acp_output_can_exceed_previous_connection_cap(protocol, tmp_path):
    script = tmp_path / "provider.py"
    script.write_text(script.read_text().replace("'text':'Done'", "'text':'x' * 2100000"))
    events = [
        event
        async for event in stream_acp(
            "grok",
            "Large answer",
            cwd=tmp_path,
            permission_mode="cli-managed",
            max_output_bytes=None,
        )
    ]
    assert events[-1]["type"] == "final"
    assert len(events[-1]["message"]["body"]) == 2100000


@pytest.mark.parametrize("provider,effort", [("grok", None), ("grok", "xhigh")])
async def test_rem_preserves_thread_context_and_routes_acp_events(
    provider, effort, protocol, tmp_path, monkeypatch
):
    from gofer.ui import chat

    async def validate(*args, **kwargs):
        return None

    monkeypatch.setattr(chat, "validate_provider_selection_async", validate)
    monkeypatch.setattr(chat, "resolve_provider_executable", lambda p: sys.executable)
    monkeypatch.setattr(chat, "ensure_local_gofer_cli", lambda data: tmp_path / "gof")
    events = [
        event
        async for event in chat.stream_workflow_chat(
            provider,
            "custom/model",
            [
                {"role": "user", "body": "Remember the original goal"},
                {"role": "assistant", "body": "The parser fix is ready"},
                {"role": "user", "body": "Continue from that fix"},
            ],
            None,
            working_dir=tmp_path,
            data_dir=tmp_path / "data",
            agent_instructions="Keep Rem's thread persona",
            permission_mode="cli-managed",
            effort=effort,
        )
    ]
    assert events[-1]["type"] == "final"
    assert events[-1]["provider"] == provider
    assert events[-1]["message"]["body"] == "Done"
    requests = [json.loads(line) for line in protocol.read_text().splitlines()]
    if effort:
        selection = next(r for r in requests if r["method"] == "session/set_model")
        assert selection["params"]["_meta"]["reasoningEffort"] == effort
    prompt = next(
        r["params"]["prompt"][0]["text"] for r in requests if r["method"] == "session/prompt"
    )
    for value in (
        "Remember the original goal",
        "The parser fix is ready",
        "Continue from that fix",
        "Keep Rem's thread persona",
    ):
        assert value in prompt


@pytest.mark.parametrize("model", ["grok-4.6", None])
async def test_grok_effort_reaches_native_session(protocol, tmp_path, model):
    events = [
        event
        async for event in stream_acp(
            "grok",
            "Hello",
            cwd=tmp_path,
            model=model,
            effort="xhigh",
            permission_mode="cli-managed",
        )
    ]
    assert events[-1]["type"] == "final"
    requests = [json.loads(line) for line in protocol.read_text().splitlines()]
    selection = next(
        r for r in requests if r["method"] == ("session/set_model" if model else "session/new")
    )
    assert selection["params"]["_meta"]["reasoningEffort"] == "xhigh"


async def test_grok_process_receives_temporary_plugin(protocol, tmp_path, monkeypatch):
    from gofer.subscriptions import acp_providers

    original = acp_providers.acp_command
    paths = []

    def command(provider, executable=None, plugin_dir=None):
        if plugin_dir:
            path = Path(plugin_dir)
            assert (path / ".mcp.json").is_file()
            paths.append(path)
        return original(provider, executable)

    monkeypatch.setattr(acp_providers, "acp_command", command)
    events = [
        event
        async for event in stream_acp("grok", "Hello", cwd=tmp_path, permission_mode="cli-managed")
    ]
    assert events[-1]["type"] == "final"
    assert len(paths) == 1
    assert not paths[0].exists()


@pytest.mark.parametrize("identity", ["granted", "foreign-session", "unknown-server", "native"])
async def test_grok_stream_wires_session_scoped_mcp_permissions(protocol, tmp_path, identity):
    from gofer.core.prompt_envelope import AgentResources

    script = tmp_path / "provider.py"
    source = script.read_text()
    source = source.replace(
        "elif method=='session/new': result={'sessionId':'fresh'}",
        "elif method=='session/new':\n"
        "  plugin=request['params']['_meta']['pluginDirs'][0]\n"
        "  alias=next(iter(json.load(open(plugin+'/.mcp.json'))['mcpServers']))\n"
        "  result={'sessionId':'fresh'}",
    )
    source = source.replace(
        "elif method=='session/prompt':\n",
        "elif method=='session/prompt':\n"
        f"  identity={identity!r}\n"
        "  params={'sessionId':'foreign' if identity=='foreign-session' else 'fresh',\n"
        "   'toolCall':{'_meta':{'x.ai/tool':{'version':1,\n"
        "    'namespace':'native' if identity=='native' else 'mcp',\n"
        "    'name':('unknown' if identity=='unknown-server' else alias)+'__read_note'}}},\n"
        "   'options':[{'kind':'allow_once','optionId':'one-time'}]}\n"
        "  print(json.dumps({'jsonrpc':'2.0','id':'permission-1',\n"
        "   'method':'session/request_permission','params':params}),flush=True)\n"
        "  answer=json.loads(sys.stdin.readline())\n"
        "  log.write(json.dumps(answer)+'\\n'); log.flush()\n",
    )
    script.write_text(source)
    resources = AgentResources.model_validate(
        {"mcpServers": [{"name": "notes", "type": "stdio", "command": "fake-notes"}]}
    )
    events = [
        event
        async for event in stream_acp(
            "grok", "Read the note", cwd=tmp_path,
            permission_mode="cli-managed", resources=resources,
        )
    ]
    assert events[-1]["type"] == "final"
    answer = next(
        json.loads(line) for line in protocol.read_text().splitlines()
        if json.loads(line).get("id") == "permission-1"
    )
    assert answer["result"] == {
        "outcome": {"outcome": "selected", "optionId": "one-time"}
        if identity == "granted" else {"outcome": "cancelled"}
    }
