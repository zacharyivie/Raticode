from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.ui.chat import build_chat_prompt
from gofer.ui.second_brain import (
    REPORT_THEME_PROMPTS,
    SecondBrain,
    serve_second_brain,
    with_second_brain,
)


def test_notes_search_external_edits_deletions_and_links(tmp_path: Path) -> None:
    brain = SecondBrain(tmp_path)
    saved = brain.call(
        "save_note", {"path": "projects/launch.md", "content": "Launch uses SQLite."}
    )
    assert str(tmp_path / "projects/launch.md") in saved["link"]
    matches = brain.search("SQLite")
    assert matches[0]["path"] == "projects/launch.md"
    stable_id = matches[0]["id"]
    (tmp_path / "projects/launch.md").write_text("Launch uses Postgres.")
    assert not brain.search("SQLite")
    assert brain.search("Postgres")[0]["id"] == stable_id
    assert brain.call("read_note", {"path": "projects/launch.md"})["content"].endswith("Postgres.")
    (tmp_path / "projects/launch.md").unlink()
    assert not brain.search("Postgres")


def test_note_format_containment_and_existing_knowledge(tmp_path: Path) -> None:
    root = tmp_path / "brain"
    root.mkdir()
    brain = SecondBrain(root, "html")
    with pytest.raises(ValueError, match="extension"):
        brain.call("save_note", {"path": "notes/topic.md", "content": "hello"})
    with pytest.raises(ValueError, match="inside"):
        brain.call("save_note", {"path": "../outside.html", "content": "hello"})
    (root / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="inside"):
        brain.call("save_note", {"path": "escape/outside.html", "content": "hello"})
    brain.call("save_note", {"path": "notes/topic.html", "content": "<h1>Original</h1>"})
    with pytest.raises(FileExistsError):
        brain.call("save_note", {"path": "notes/topic.html", "content": "replacement"})
    assert "Original" in (root / "notes/topic.html").read_text()


def test_second_brain_mcp_lifecycle_and_errors(tmp_path: Path) -> None:
    source = io.StringIO(
        "\n".join(
            json.dumps(item)
            for item in [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "rules"}},
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "read_note", "arguments": {"path": "missing.md"}},
                },
            ]
        )
    )
    output = io.StringIO()
    serve_second_brain(tmp_path, input_stream=source, output_stream=output)
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [item["id"] for item in responses] == [1, 2, 3, 4]
    assert responses[0]["result"]["capabilities"] == {"tools": {}}
    assert {item["name"] for item in responses[1]["result"]["tools"]} == {
        "rules",
        "search",
        "read_note",
        "save_note",
    }
    assert str(tmp_path) in responses[2]["result"]["content"][0]["text"]
    assert responses[3]["result"]["isError"] is True


def test_tool_is_added_only_when_enabled_even_without_shell(tmp_path: Path) -> None:
    disabled = {"remSecondBrain": {"enabled": False}}
    assert with_second_brain(disabled, None) is disabled
    workflow = {
        "remResources": {"shell": False},
        "remSecondBrain": {
            "enabled": True,
            "root": str(tmp_path),
            "format": "html",
            "theme": "sepia",
        },
    }
    configured = with_second_brain(workflow, Path("/trusted/gof"))
    assert configured is not None
    assert configured["remResources"]["shell"] is False
    tool = configured["remResources"]["mcpServers"][0]
    assert tool["name"] == "second_brain"
    assert tool["command"] == "/trusted/gof"
    assert tool["args"][-4:] == ["--report-format", "html", "--report-theme", "sepia"]
    prompt = build_chat_prompt("codex", "test", [{"role": "user", "body": "Review"}], configured)
    assert "search this folder" in prompt
    assert "HTML" in prompt
    assert "warm paper tones" in prompt
    assert "Second Brain is enabled" not in build_chat_prompt("codex", "test", [], disabled)


def test_native_cli_serves_mcp_in_packaged_command_tree(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["ui", "second-brain", "--root", str(tmp_path)],
        input='{"id":1,"method":"tools/list"}\n',
    )
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.output)["result"]["tools"]) == 4


@pytest.mark.parametrize("inherited", [False, True])
def test_codex_grants_only_builtin_second_brain_tools(
    tmp_path: Path, monkeypatch, inherited: bool
) -> None:
    from gofer.core.prompt_envelope import AgentResources
    from gofer.ui.chat import _build_chat_command

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    if inherited:
        (tmp_path / "codex").mkdir()
        (tmp_path / "codex" / "config.toml").write_text(
            '[mcp_servers.second_brain]\nurl="https://old.example/mcp"\n'
        )
    server_name = "raticode_second_brain_1" if inherited else "second_brain"
    cli = tmp_path / "gof"
    workflow = with_second_brain({"remSecondBrain": {"enabled": True, "root": str(tmp_path)}}, cli)
    assert workflow is not None
    resources = AgentResources.model_validate(workflow["remResources"])
    kwargs = dict(
        provider="codex",
        model="cli-default",
        prompt="Read notes",
        resources=resources,
        working_dir=tmp_path,
        data_dir=tmp_path,
    )
    command = _build_chat_command(**kwargs, second_brain_cli_path=cli)
    assert (
        f'mcp_servers.{server_name}.enabled_tools=["rules", "search", "read_note", "save_note"]'
        in command
    )
    for tool in ("rules", "search", "read_note", "save_note"):
        assert f'mcp_servers.{server_name}.tools.{tool}.approval_mode="approve"' in command
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert not any("approval_mode" in arg for arg in _build_chat_command(**kwargs))
    resources.mcpServers[0].command = "/untrusted/gof"
    assert not any(
        "approval_mode" in arg for arg in _build_chat_command(**kwargs, second_brain_cli_path=cli)
    )


@pytest.mark.parametrize("theme", REPORT_THEME_PROMPTS)
def test_html_reports_preserve_authored_styles(tmp_path: Path, theme: str) -> None:
    brain = SecondBrain(tmp_path, "html", theme)
    original = "<html><HEAD><style>p{color:black}</style></HEAD><body><p>Keep me</p></body></html>"
    brain.call("save_note", {"path": "report.html", "content": original})
    assert (tmp_path / "report.html").read_text() == original


@pytest.mark.parametrize(
    "theme, expected",
    [
        ("auto", "prefers-color-scheme"),
        ("light", "luminous editorial palette"),
        ("dark", "deep surfaces"),
        ("sepia", "warm paper tones"),
        ("vaporwave", "Vaporwave:"),
        ("steam", "Steam:"),
        ("carbon", "Carbon:"),
        ("botanical", "Botanical:"),
        ("blueprint", "Blueprint:"),
        ("arcade", "Arcade:"),
        ("sakura", "Sakura:"),
        ("deep-sea", "Deep Sea:"),
        ("solarpunk", "Solarpunk:"),
        ("noir", "Noir:"),
        ("candy-lab", "Candy Lab:"),
        ("cosmic", "Cosmic:"),
    ],
)
def test_cli_exposes_theme_in_initialization_and_rules(
    tmp_path: Path, theme: str, expected: str
) -> None:
    result = CliRunner().invoke(
        app,
        [
            "ui",
            "second-brain",
            "--root",
            str(tmp_path),
            "--report-format",
            "html",
            "--report-theme",
            theme,
        ],
        input='{"id":1,"method":"initialize"}\n'
        '{"id":2,"method":"tools/call","params":{"name":"rules"}}\n',
    )
    assert result.exit_code == 0, result.output
    initialized, rules = [json.loads(line)["result"] for line in result.output.splitlines()]
    instructions = initialized["instructions"]
    assert expected in instructions
    assert "creative freedom" in instructions
    assert "report-specific CSS" in instructions
    assert json.loads(rules["content"][0]["text"]) == instructions
    assert "HTML design direction" not in SecondBrain(tmp_path, "md", theme).call("rules", {})
