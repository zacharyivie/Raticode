from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from gofer.core.agent import AgentResult
from gofer.core.provider_capabilities import CLI_PROVIDERS
from gofer.ui import rem_home_provider as provider_module


@pytest.mark.parametrize("provider", CLI_PROVIDERS)
def test_conversation_provider_has_no_working_project_or_resource_grants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
):
    observed: dict[str, Any] = {}

    class Agent:
        def __init__(self, config, subscription):
            observed["config"] = config
            observed["subscription"] = subscription

        async def run(self, **kwargs):
            observed.update(kwargs)
            return AgentResult(
                agent_id="Rem",
                success=True,
                output="",
                message='{"reply":"Hi"}',
                exit_code=0,
                duration_seconds=0,
            )

    monkeypatch.setattr(provider_module, "Agent", Agent)
    result = provider_module.generate_decision(
        "Say hello",
        {"provider": provider, "model": "cli-default", "resources": {"shell": True}},
        threading.Event(),
        tmp_path,
    )
    assert result == {"reply": "Hi"}
    settings = observed["provider_settings"]
    assert settings.tools == [] and settings.mcp_servers == []
    assert observed["config"].working_dir != tmp_path
    assert not observed["config"].working_dir.exists()
    if provider == "codex":
        assert settings.sandbox_mode == "read-only"
        assert "features.shell_tool=false" in settings.extra_args
        assert 'web_search="disabled"' in settings.extra_args
        assert "features.apps=false" in settings.extra_args
        assert "features.hooks=false" in settings.extra_args
    elif provider == "claude_code":
        assert settings.extra_args[settings.extra_args.index("--tools") + 1] == ""
    else:
        assert observed["subscription"].tool_free is True
        if provider in {"grok", "antigravity"}:
            assert settings.approval_mode == "cli-managed"


def test_unknown_conversation_provider_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="Unknown provider"):
        provider_module.generate_decision(
            "Hi", {"provider": "unknown"}, threading.Event(), tmp_path
        )


def test_grok_conversation_native_flags_and_literal_prompt(tmp_path: Path, monkeypatch):
    from gofer.subscriptions import grok_headless

    executable = tmp_path / "fake-grok"
    log = tmp_path / "invocation.json"
    executable.write_text(
        f"#!{sys.executable}\nimport json,sys,pathlib,os\n"
        "args=sys.argv[1:]\n"
        "path=args[args.index('--prompt-file')+1]\n"
        f"pathlib.Path({str(log)!r}).write_text(json.dumps({{"
        "'args':args,'prompt':pathlib.Path(path).read_text(),'cwd':os.getcwd()}))\n"
        "print(json.dumps({'type':'result','subtype':'success','result':'{\"reply\":\"Hi\"}'}))\n"
    )
    executable.chmod(0o700)
    monkeypatch.setattr(grok_headless, "resolve_provider_executable", lambda p: str(executable))
    prompt = 'Hi $(never execute this)\n"quoted" ★'
    assert provider_module.generate_decision(
        prompt,
        {"provider": "grok", "model": "grok-test", "effort": "high"},
        threading.Event(),
        tmp_path,
    ) == {"reply": "Hi"}
    saved = json.loads(log.read_text())
    args = saved["args"]
    assert json.loads(saved["prompt"].split("\n\n", 1)[1])["request"] == prompt
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    assert args[args.index("--model") + 1] == "grok-test"
    assert args[args.index("--reasoning-effort") + 1] == "high"
    assert "--no-subagents" in args and "--disable-web-search" in args
    assert "MCPTool" in args and "Bash" in args and "Write" in args
    assert not Path(saved["cwd"]).exists()
    assert not Path(args[args.index("--prompt-file") + 1]).exists()


def test_antigravity_conversation_uses_private_no_tool_agent(tmp_path: Path, monkeypatch):
    from gofer.subscriptions import antigravity

    observed = {}

    async def fake(command, **kwargs):
        observed["command"] = command
        observed["prompt"] = json.loads(kwargs["stdin"])["message"]["content"]
        observed["agent"] = (
            kwargs["cwd"] / ".agents/agents/raticode-rem-conversation.md"
        ).read_text()
        yield {
            "type": "chunk",
            "stream": "stdout",
            "text": json.dumps(
                {
                    "event": "result",
                    "result": {"status": "SUCCESS", "response": '{"reply":"Hi"}'},
                }
            )
            + "\n",
        }
        yield {"type": "exit", "returncode": 0}

    monkeypatch.setattr(antigravity, "resolve_provider_executable", lambda p: "/fake/agy")
    monkeypatch.setattr(antigravity, "stream_subprocess", fake)
    assert provider_module.generate_decision(
        "Hi",
        {"provider": "antigravity", "model": "gemini-test", "effort": "high"},
        threading.Event(),
        tmp_path,
    ) == {"reply": "Hi"}
    assert json.loads(observed["prompt"].split("\n\n", 1)[1])["request"] == "Hi"
    assert "tools: []" in observed["agent"] and "commandExecutionPolicy: off" in observed["agent"]
    args = observed["command"]
    assert args[args.index("--agent") + 1] == "raticode-rem-conversation"
    assert args[args.index("--model") + 1] == "gemini-test"
    assert "--dangerously-skip-permissions" not in args
    assert not (tmp_path / ".agents").exists()


def test_only_backend_artifact_endpoint_receives_save_permission(tmp_path: Path):
    from gofer.core.prompt_envelope import AgentResources
    from gofer.ui.chat import _build_chat_command

    url = "http://127.0.0.1:1234/private-capability"
    resources = AgentResources.model_validate(
        {"mcpServers": [{"name": "rem_artifacts", "url": url}]}
    )
    options = dict(
        provider="codex",
        model="cli-default",
        prompt="Research",
        data_dir=tmp_path,
        working_dir=tmp_path,
        resources=resources,
        permission_mode="read-only",
    )
    trusted = _build_chat_command(**options, trusted_rem_artifacts_url=url)
    untrusted = _build_chat_command(**options, trusted_rem_artifacts_url=url + "-other")
    assert any('tools.save_report.approval_mode="approve"' in arg for arg in trusted)
    assert not any('tools.save_report.approval_mode="approve"' in arg for arg in untrusted)


@pytest.mark.parametrize("permission", ["workspace-write", "read-only"])
@pytest.mark.parametrize("network", [True, False])
def test_codex_network_access_preserves_filesystem_permissions(tmp_path, permission, network):
    from gofer.ui.chat import _build_chat_command

    command = _build_chat_command(
        "codex",
        "cli-default",
        "Run the requested workflow",
        working_dir=tmp_path,
        data_dir=tmp_path,
        permission_mode=permission,
        network_access=network,
    )
    assert command[command.index("--sandbox") + 1] == permission
    assert ("sandbox_workspace_write.network_access=true" in command) is (
        network and permission == "workspace-write"
    )
