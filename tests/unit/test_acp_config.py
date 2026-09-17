"""Session injection fixtures from Antigravity and Grok 37949780."""

import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest

from gofer.core.prompt_envelope import AgentResources, McpReference
from gofer.subscriptions.acp_config import (
    acp_session_config,
    deny_acp_permission,
    grok_mcp_permission_handler,
)
from gofer.subscriptions.acp_transport import AcpTransportError, open_acp_transport
from tests.unit.test_acp_session import fake_agent


@pytest.mark.parametrize("provider", ["antigravity", "grok"])
def test_run_injection_has_unique_identity_and_preserves_auth(provider, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    auth = home / "auth.json"
    auth.write_text("private existing credentials")
    monkeypatch.setenv("HOME", str(home))
    before = dict(os.environ)
    url = "http://127.0.0.1:12345/mcp?token=TEST_ONLY"
    resources = AgentResources(mcpServers=[McpReference(name="swarm", url=url)])
    paths = []
    with acp_session_config(provider, tmp_path, resources, trusted_swarm_url=url) as first:
        with acp_session_config(provider, tmp_path, resources, trusted_swarm_url=url) as second:
            assert first.grants.keys().isdisjoint(second.grants.keys())
            for config in [first, second]:
                alias = next(iter(config.grants))
                assert config.grants[alias] == {"swarm_action"}
                assert config.params["cwd"] == str(tmp_path)
                if provider == "antigravity":
                    assert config.params["mcpServers"] == [
                        {"name": alias, "type": "http", "url": url, "headers": []}
                    ]
                else:
                    path = Path(config.params["_meta"]["pluginDirs"][0])
                    paths.append(path)
                    assert path.stat().st_mode & 0o077 == 0
                    assert json.loads((path / ".mcp.json").read_text()) == {
                        "mcpServers": {alias: {"type": "http", "url": url, "headers": {}}}
                    }
                    assert config.params["mcpServers"] == []
        if paths:
            assert paths[0].exists() and not paths[1].exists()
    assert all(not path.exists() for path in paths)
    assert auth.read_text() == "private existing credentials"
    assert dict(os.environ) == before


@pytest.mark.parametrize("provider", ["antigravity", "grok"])
def test_untrusted_swarm_endpoint_never_injected(provider, tmp_path):
    resources = AgentResources(
        mcpServers=[McpReference(name="swarm", url="http://wrong.example/mcp")]
    )
    with pytest.raises(ValueError, match="trusted running turn"):
        with acp_session_config(provider, tmp_path, resources):
            pytest.fail("Untrusted endpoint reached session config")


async def test_grok_plugin_outlives_cancelled_process(tmp_path):
    cancelled = threading.Event()
    with acp_session_config("grok", tmp_path, AgentResources()) as config:
        plugin = Path(config.params["_meta"]["pluginDirs"][0])
        command = fake_agent(
            tmp_path,
            "request = read()\n"
            f"assert __import__('pathlib').Path({str(plugin)!r}).exists()\n"
            "send({'id': request['id'], 'result': {}})\ntime.sleep(30)\n",
        )
        async with open_acp_transport(command, cwd=tmp_path, cancel_event=cancelled) as rpc:
            await rpc.request("probe", {})
            cancelled.set()
            with pytest.raises(AcpTransportError, match="cancelled"):
                await rpc.next_notification()
            assert plugin.exists()
        assert rpc.process.returncode is not None
        assert plugin.exists()
    assert not plugin.exists()


async def test_title_only_permission_is_denied_even_for_trusted_name():
    result = await deny_acp_permission(
        "session/request_permission",
        {"toolCall": {"title": "swarm_action", "kind": "other", "toolCallId": "swarm_action"}},
    )
    assert result == {"outcome": {"outcome": "cancelled"}}
    with pytest.raises(ValueError, match="Unsupported"):
        await deny_acp_permission("fs/write_text_file", {"path": "/tmp/should-not-write"})


@pytest.mark.parametrize(
    "change",
    [
        None,
        "title_only",
        "foreign",
        "lookalike",
        "namespace",
        "version",
        "bool_version",
        "always",
        "duplicate",
        "request_meta",
    ],
)
async def test_grok_permission_uses_canonical_session_identity_and_offered_once(change):
    grants = {"raticode-run": {"swarm_action"}}
    handler = grok_mcp_permission_handler("session", grants)
    grants["raticode-run"].add("untrusted_later_mutation")
    identity: dict[str, Any] = {
        "version": 1, "namespace": "mcp", "name": "raticode-run__swarm_action"
    }
    params: dict[str, Any] = {
        "sessionId": "session",
        "toolCall": {"title": "swarm_action", "_meta": {"x.ai/tool": identity}},
        "options": [{"kind": "allow_once", "optionId": "offered-once"}],
    }
    if change == "title_only":
        params["toolCall"].pop("_meta")
    elif change == "request_meta":
        params["_meta"] = params["toolCall"].pop("_meta")
    elif change == "foreign":
        params["sessionId"] = "foreign"
    elif change == "lookalike":
        identity["name"] += "_malicious"
    elif change == "namespace":
        identity["namespace"] = "native"
    elif change == "version":
        identity["version"] = 2
    elif change == "bool_version":
        identity["version"] = True
    elif change == "always":
        params["options"][0]["kind"] = "allow_always"
    elif change == "duplicate":
        params["options"].append({"kind": "reject_once", "optionId": "offered-once"})
    result = await handler("session/request_permission", params)
    assert result == (
        {"outcome": {"outcome": "selected", "optionId": "offered-once"}}
        if change is None
        else {"outcome": {"outcome": "cancelled"}}
    )
    identity["name"] = "raticode-run__untrusted_later_mutation"
    assert await handler("session/request_permission", params) == {
        "outcome": {"outcome": "cancelled"}
    }
