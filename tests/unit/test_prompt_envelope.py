from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from gofer.core.prompt_envelope import (
    BROWSER_MCP_PACKAGE,
    AgentResources,
    McpReference,
    playwright_browser_args,
    resource_cli_args,
    resource_index,
    with_browser,
)
from gofer.ui.chat import build_chat_prompt


def test_resource_selection_maps_to_both_providers_without_inlining_skills(tmp_path: Path) -> None:
    skill = tmp_path / "large-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("PRIVATE IMPLEMENTATION" * 1000)
    resources = AgentResources.model_validate(
        {
            "shell": False,
            "web": True,
            "skills": [{"path": str(skill)}],
            "mcpServers": [{"name": "docs", "url": "https://example.com/mcp"}],
        }
    )
    index = resource_index(resources)
    assert str(skill / "SKILL.md") in index
    assert "PRIVATE IMPLEMENTATION" not in index
    assert "https://" not in index
    codex = resource_cli_args("codex", resources)
    claude = resource_cli_args("claude_code", resources)
    assert "features.shell_tool=false" in codex
    assert 'web_search="live"' in codex
    assert any(arg.startswith("mcp_servers.docs={enabled=true,") for arg in codex)
    assert "Bash" not in claude[claude.index("--tools") + 1]
    assert "WebSearch" in claude[claude.index("--tools") + 1]
    assert "--strict-mcp-config" in claude
    assert json.loads(claude[-1])["mcpServers"]["docs"]["url"] == "https://example.com/mcp"


def test_backend_defaults_match_the_menu_defaults() -> None:
    menu = Path(__file__).parents[2] / "frontend/src/lib/remResources.js"
    defaults = AgentResources().model_dump()
    line = next(row for row in menu.read_text().splitlines() if "DEFAULT_REM_RESOURCES =" in row)
    for key in ("shell", "web", "agents", "browser"):
        assert f"{key}: {str(defaults[key]).lower()}" in line


@pytest.mark.parametrize("agents", [True, False])
def test_subagents_switch_maps_to_codex_and_claude(agents: bool) -> None:
    resources = AgentResources(agents=agents)
    codex = resource_cli_args("codex", resources)
    claude = resource_cli_args("claude_code", resources)
    assert f"features.multi_agent={str(agents).lower()}" in codex
    assert ("Agent" in claude[claude.index("--tools") + 1].split(",")) is agents


def test_codex_pins_features_the_menu_does_not_offer() -> None:
    codex = resource_cli_args("codex", AgentResources())
    for feature in ("apps", "browser_use", "computer_use", "image_generation"):
        assert f"features.{feature}=false" in codex
    assert "tools.view_image=true" in codex


def test_claude_file_access_includes_notebooks_and_code_intelligence() -> None:
    claude = resource_cli_args("claude_code", AgentResources.none())
    assert claude[claude.index("--tools") + 1].split(",") == [
        "Read",
        "Edit",
        "Write",
        "NotebookEdit",
        "Glob",
        "Grep",
        "LSP",
    ]


def test_no_resources_turns_every_built_in_off() -> None:
    assert AgentResources.none().model_dump() == {
        "shell": False,
        "web": False,
        "agents": False,
        "browser": False,
        "skills": [],
        "mcpServers": [],
    }


def test_browser_switch_adds_a_pinned_playwright_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    assert with_browser(AgentResources()).mcpServers == []
    server = with_browser(AgentResources(browser=True)).mcpServers[0]
    assert (server.name, server.type, server.command) == ("browser", "stdio", "/usr/bin/npx")
    assert server.args == ["-y", BROWSER_MCP_PACKAGE, "--headless", "--isolated"]
    assert "@latest" not in BROWSER_MCP_PACKAGE


def test_playwright_uses_an_installed_chromium_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed: dict[str, str] = {}
    monkeypatch.setattr("shutil.which", lambda name: installed.get(name))
    monkeypatch.setattr("gofer.core.prompt_envelope._CHROME_PATHS", {})
    monkeypatch.setattr("gofer.core.prompt_envelope._EDGE_PATHS", {})
    monkeypatch.setattr("gofer.core.prompt_envelope._CHROMIUM_PATHS", {})
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    # Nothing installed: Playwright's own Chromium reports how to install it.
    assert playwright_browser_args() == ["--browser=chromium"]
    for revision in ("1234", "1248"):
        executable = tmp_path / f"chromium-{revision}" / "chrome-linux64" / "chrome"
        executable.parent.mkdir(parents=True)
        executable.write_text("")
    (tmp_path / "chromium_headless_shell-1300").mkdir()
    newest = tmp_path / "chromium-1248" / "chrome-linux64" / "chrome"
    assert playwright_browser_args() == ["--browser=chromium", f"--executable-path={newest}"]
    installed["chromium"] = "/usr/bin/chromium"
    assert playwright_browser_args() == [
        "--browser=chromium",
        "--executable-path=/usr/bin/chromium",
    ]
    installed["microsoft-edge"] = "/usr/bin/microsoft-edge"
    assert playwright_browser_args() == ["--browser=msedge"]
    installed["google-chrome"] = "/usr/bin/google-chrome"
    assert playwright_browser_args() == []


def test_browser_targets_choose_the_browser_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    resources = AgentResources(browser=True)
    visible = with_browser(resources, "external").mcpServers[0]
    assert visible.args == ["-y", BROWSER_MCP_PACKAGE, "--isolated"]
    own = McpReference(name="raticode", type="stdio", command="/bin/gof", args=["ui", "browser"])
    server = with_browser(resources, "raticode", raticode_server=own).mcpServers[0]
    assert (server.name, server.command, server.args) == ("browser", "/bin/gof", ["ui", "browser"])
    with pytest.raises(ValueError, match="desktop app"):
        with_browser(resources, "raticode")


def test_browser_switch_keeps_a_selected_server_named_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    own = {"name": "browser", "type": "stdio", "command": "my-browser"}
    resources = AgentResources.model_validate({"browser": True, "mcpServers": [own]})
    assert with_browser(resources).mcpServers[0].command == "my-browser"
    with pytest.raises(ValueError, match="needs Node.js"):
        with_browser(AgentResources(browser=True))


@pytest.mark.parametrize("name", ["node_repl", "ambient", "my-server"])
def test_codex_thread_disables_inherited_mcp_servers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    config = f'[mcp_servers.{name}]\ncommand="unused-mcp-executable"\n'
    (tmp_path / "config.toml").write_text(config)
    args = resource_cli_args("codex", AgentResources(), tmp_path)
    assert f"mcp_servers.{name}.enabled=false" in args

    # Match Codex's CLI override semantics: only values are parsed as TOML;
    # key segments are used literally, including any quote characters.
    document = tomllib.loads(config)
    for override in args[1::2]:
        key, value = override.split("=", 1)
        if not key.startswith("mcp_servers."):
            continue
        target = document
        *parents, field = key.split(".")
        for parent in parents:
            target = target.setdefault(parent, {})
        target[field] = tomllib.loads(f"value={value}")["value"]
    assert document["mcp_servers"] == {name: {"command": "unused-mcp-executable", "enabled": False}}


def test_rem_keeps_early_messages_and_persona_when_switching_provider() -> None:
    messages = [{"role": "user", "body": f"turn {index}"} for index in range(20)]
    for provider in ("codex", "claude_code"):
        prompt = build_chat_prompt(provider, "cli-default", messages, None)
        envelope = json.loads("{" + prompt.split("\n\n{", 1)[1])
        assert "USER: turn 0\n" in envelope["request"]
        assert "USER: turn 19" in envelope["request"]
        assert prompt.startswith("You are Rem, the coding agent for Raticode.")
        assert "Rem stands for Raticode Environment Manager." in prompt
        assert 'always use "Rem"' in prompt
        assert 'never "REM"' in prompt
        assert "<gofer_flow_skill>" not in prompt


def test_codex_disables_inherited_server_with_dots_without_creating_a_broken_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    names = ["docs.with.dots", "another.dotted.server", "plain"]
    config = "\n".join(
        f'[mcp_servers.{json.dumps(name)}]\ncommand="unused-mcp-executable"' for name in names
    )
    (tmp_path / "config.toml").write_text(config)
    args = resource_cli_args("codex", AgentResources(), tmp_path)
    overrides = [arg for arg in args if arg.startswith("mcp_servers=")]
    assert len(overrides) == 1
    disabled = tomllib.loads(overrides[0])["mcp_servers"]
    assert disabled == {name: {"enabled": False} for name in names}
    assert not any(arg.startswith("mcp_servers.docs.") for arg in args)


def test_mcp_endpoint_rejects_embedded_credentials() -> None:
    with pytest.raises(ValidationError, match="without credentials"):
        AgentResources.model_validate(
            {
                "mcpServers": [
                    {
                        "name": "docs",
                        "url": "https://user:password@example.com/mcp",
                    }
                ]
            }
        )


def test_duplicate_mcp_names_are_rejected_before_provider_configuration() -> None:
    with pytest.raises(ValidationError, match="names must be unique"):
        AgentResources.model_validate(
            {
                "mcpServers": [
                    {"name": "docs", "url": "https://example.com/first"},
                    {"name": "docs", "type": "stdio", "command": "other-server"},
                ]
            }
        )


def test_chat_prompt_identifies_current_tools_after_prior_missing_tool_reply() -> None:
    prompt = build_chat_prompt(
        "codex",
        "cli-default",
        [
            {"role": "assistant", "body": "I have no web search tool."},
            {"role": "user", "body": "I enabled it. Search now."},
        ],
        {"remResources": {"shell": False, "web": True}},
    )
    assert (
        "Current turn tools: run commands=false, web search=true, subagents=true, browser=false."
        in prompt
    )
    assert "previous configuration" in prompt


def test_rem_asks_for_browser_instead_of_opening_the_system_browser() -> None:
    messages = [{"role": "user", "body": "Navigate to Theo's YouTube channel."}]
    off = build_chat_prompt("claude_code", "cli-default", messages, {"remResources": {}})
    assert "Browser is off for this thread" in off
    assert "do not open it with xdg-open" in off
    on = build_chat_prompt(
        "claude_code", "cli-default", messages, {"remResources": {"browser": True}}
    )
    assert "Browser is off for this thread" not in on


def test_stdio_resources_pass_separate_arguments_to_both_providers() -> None:
    import tomllib

    resources = AgentResources.model_validate(
        {
            "mcpServers": [
                {
                    "name": "local",
                    "type": "stdio",
                    "command": "/program with spaces/mcp",
                    "args": ["--root", "/project with spaces", "literal; argument"],
                }
            ]
        }
    )
    codex = resource_cli_args("codex", resources)
    entry = next(item for item in codex if item.startswith("mcp_servers.local="))
    configured = tomllib.loads(entry)["mcp_servers"]["local"]
    assert configured["command"] == "/program with spaces/mcp"
    assert configured["args"] == resources.mcpServers[0].args
    claude = resource_cli_args("claude_code", resources)
    assert json.loads(claude[-1])["mcpServers"]["local"]["args"] == configured["args"]


@pytest.mark.parametrize("inherited_stdio", [True, False])
@pytest.mark.parametrize("selected_stdio", [True, False])
def test_codex_selected_server_does_not_merge_inherited_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inherited_stdio: bool, selected_stdio: bool
) -> None:
    from copy import deepcopy

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    old_transport = (
        'command="old"\nargs=["old"]\nenv={OLD="old"}'
        if inherited_stdio
        else ('url="https://old.example/mcp"\nhttp_headers={Authorization="old"}')
    )
    config = "[mcp_servers.docs]\n" + old_transport + '\nenabled_tools=["old"]\n'
    (tmp_path / "config.toml").write_text(config)
    resources = AgentResources.model_validate(
        {
            "mcpServers": [
                {
                    "name": "docs",
                    "type": "stdio" if selected_stdio else "http",
                    "command": "new",
                    "args": ["new"],
                    "url": "https://new.example/mcp",
                }
            ]
        }
    )
    document = tomllib.loads(config)
    original = deepcopy(document["mcp_servers"]["docs"])
    args = resource_cli_args("codex", resources, tmp_path)

    # Codex recursively merges tables, including inline tables from CLI overrides.
    def merge(target: dict[str, Any], incoming: dict[str, Any]) -> None:
        for key, value in incoming.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(target[key], value)
            else:
                target[key] = value

    for override in args[1::2]:
        key, raw = override.split("=", 1)
        value = tomllib.loads(f"value={raw}")["value"]
        for part in reversed(key.split(".")):
            value = {part: value}
        merge(document, value)
    servers = document["mcp_servers"]
    assert servers.pop("docs") == {**original, "enabled": False}
    assert list(servers.values()) == [
        {"enabled": True, "command": "new", "args": ["new"]}
        if selected_stdio
        else {"enabled": True, "url": "https://new.example/mcp"}
    ]
    assert (tmp_path / "config.toml").read_text() == config


def test_codex_server_alias_avoids_inherited_and_selected_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.core.prompt_envelope import codex_mcp_server_names

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('[mcp_servers.docs]\ncommand="old"\n')
    project_config = tmp_path / ".codex" / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text('[mcp_servers.raticode_docs_1]\ncommand="old"\n')
    resources = AgentResources.model_validate(
        {
            "mcpServers": [
                {"name": name, "url": "https://example.com/mcp"}
                for name in ["docs", "raticode_docs_2"]
            ]
        }
    )
    assert codex_mcp_server_names(resources, tmp_path) == {
        "docs": "raticode_docs_3",
        "raticode_docs_2": "raticode_docs_2",
    }
