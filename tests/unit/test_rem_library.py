from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.core import rem_library
from gofer.core.prompt_envelope import AgentResources, resource_cli_args, resource_index
from gofer.subscriptions.cli_providers import cli_invocation
from gofer.ui.chat import _skill_read_paths
from gofer.ui.server import GoferUiServer


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    return home


def write_skill(folder: Path, name: str, description: str) -> Path:
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: >\n  {description}\n  continued\n---\n\n# Body\n",
        encoding="utf-8",
    )
    return folder


def test_discovery_lists_raticode_provider_and_registered_skills(
    tmp_path: Path, isolated_home: Path
) -> None:
    data = tmp_path / "data"
    write_skill(data / "skills" / "release-notes", "release-notes", "Write release notes")
    write_skill(isolated_home / ".claude" / "skills" / "review", "review", "Review code")
    write_skill(isolated_home / ".codex" / "skills" / ".system", "hidden", "Ignored")
    (isolated_home / ".agents" / "skills" / "no-skill-file").mkdir(parents=True)
    external = write_skill(tmp_path / "elsewhere" / "deploy", "deploy", "Ship builds")

    rem_library.add_skill(data, str(external / "SKILL.md"), added_by="cli")
    skills = {skill["name"]: skill for skill in rem_library.discover_skills(data)}

    assert set(skills) == {"release-notes", "review", "deploy"}
    assert skills["release-notes"]["origin"] == "raticode"
    assert skills["release-notes"]["description"] == "Write release notes continued"
    assert skills["review"]["origin"] == "claude"
    assert skills["deploy"] == {
        "path": str(external),
        "name": "deploy",
        "description": "Ship builds continued",
        "origin": "added",
        "available": True,
        "registered": True,
        "addedBy": "cli",
        "addedAt": skills["deploy"]["addedAt"],
    }
    with pytest.raises(ValueError, match="No SKILL.md"):
        rem_library.add_skill(data, str(tmp_path / "missing"))
    assert rem_library.remove_skill(data, str(external)) is True
    assert "deploy" not in {skill["name"] for skill in rem_library.discover_skills(data)}


def test_create_skill_scaffolds_a_discoverable_folder(tmp_path: Path) -> None:
    created = rem_library.create_skill(tmp_path, "triage", "Sort new issues", "Label issues.")
    assert created["path"] == str(tmp_path / "skills" / "triage")
    assert "Label issues." in (tmp_path / "skills" / "triage" / "SKILL.md").read_text()
    assert [skill["name"] for skill in rem_library.discover_skills(tmp_path)] == ["triage"]
    with pytest.raises(ValueError, match="already exists"):
        rem_library.create_skill(tmp_path, "triage", "Again")
    with pytest.raises(ValueError, match="Skill names"):
        rem_library.create_skill(tmp_path, "../escape", "Nope")


def test_mcp_servers_validate_names_and_update_in_place(tmp_path: Path) -> None:
    rem_library.save_mcp_server(tmp_path, {"name": "docs", "url": "https://example.com/mcp"})
    with pytest.raises(ValueError, match="already exists"):
        rem_library.save_mcp_server(tmp_path, {"name": "docs", "url": "https://example.com/2"})
    with pytest.raises(ValueError, match="reserved"):
        rem_library.save_mcp_server(tmp_path, {"name": "second_brain", "url": "https://x.dev"})
    with pytest.raises(ValueError, match="HTTP endpoint"):
        rem_library.save_mcp_server(tmp_path, {"name": "bad", "url": "https://u:p@x.dev"})
    rem_library.save_mcp_server(
        tmp_path,
        {"name": "docs", "type": "stdio", "command": "docs-mcp", "args": ["--root", "/a b"]},
        replace="docs",
    )
    (server,) = rem_library.snapshot(tmp_path)["mcpServers"]
    assert (server["type"], server["command"], server["args"]) == (
        "stdio",
        "docs-mcp",
        ["--root", "/a b"],
    )
    assert rem_library.remove_mcp_server(tmp_path, "docs") is True
    assert rem_library.remove_mcp_server(tmp_path, "docs") is False


def test_library_links_resolve_to_current_definitions_for_every_turn(tmp_path: Path) -> None:
    rem_library.save_mcp_server(tmp_path, {"name": "docs", "url": "https://new.example/mcp"})
    workflow: dict[str, Any] = {
        "projectRoot": "/repo",
        "remResources": {
            "shell": False,
            "mcpServers": [
                {"name": "docs", "url": "https://stale.example/mcp", "library": True},
                {"name": "gone", "url": "https://gone.example/mcp", "library": True},
                {"name": "local", "type": "stdio", "command": "tool"},
            ],
        },
    }

    resolved = rem_library.with_library_resources(workflow, tmp_path)

    assert resolved is not None
    servers = resolved["remResources"]["mcpServers"]
    assert [server["name"] for server in servers] == ["docs", "local"]
    assert servers[0]["url"] == "https://new.example/mcp"
    assert workflow["remResources"]["mcpServers"][0]["url"] == "https://stale.example/mcp"
    resources = AgentResources.model_validate(resolved["remResources"])
    claude = json.loads(resource_cli_args("claude_code", resources)[-1])
    assert claude["mcpServers"]["docs"] == {"type": "http", "url": "https://new.example/mcp"}
    codex = resource_cli_args("codex", resources, tmp_path)
    assert 'mcp_servers.docs={enabled=true,url="https://new.example/mcp"}' in codex
    unchanged = {"remResources": {"mcpServers": [{"name": "x", "url": "https://x.dev"}]}}
    assert rem_library.with_library_resources(unchanged, tmp_path) is unchanged


def test_skill_index_describes_skills_and_restricted_clis_can_read_them(tmp_path: Path) -> None:
    folder = write_skill(tmp_path / "skills" / "deploy", "deploy", "Ship builds")
    workflow = {"remResources": {"skills": [{"path": str(folder)}, {"path": "/missing"}]}}
    resources = AgentResources.model_validate(workflow["remResources"])

    assert json.loads(resource_index(resources))[0] == {
        "kind": "skill",
        "name": "deploy",
        "description": "Ship builds continued",
        "path": str(folder / "SKILL.md"),
    }
    paths = _skill_read_paths(workflow)
    assert paths == [folder.resolve()]
    with cli_invocation("opencode", ["opencode"], resources, extra_paths=paths) as (_, env):
        allowed = json.loads(env["OPENCODE_PERMISSION"])["external_directory"]
    assert allowed[str(folder.resolve())] == "allow"
    assert allowed[str(folder.resolve()) + "/**"] == "allow"


def test_cli_registers_servers_and_skills_for_agents(tmp_path: Path) -> None:
    runner = CliRunner()
    data = ["--data-dir", str(tmp_path)]

    result = runner.invoke(
        app,
        ["rem", "mcp", "add", "github", *data, "--", "npx", "-y", "@scope/server-github"],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app, ["rem", "mcp", "add", "docs", "--url", "https://example.com/mcp", "-d", "Docs", *data]
    )
    assert result.exit_code == 0, result.output
    assert runner.invoke(app, ["rem", "mcp", "add", "both", *data]).exit_code == 1
    result = runner.invoke(app, ["rem", "skill", "new", "triage", "-d", "Sort new issues", *data])
    assert result.exit_code == 0, result.output

    listing = json.loads(runner.invoke(app, ["rem", "resources", "--format", "json", *data]).output)
    servers = {server["name"]: server for server in listing["mcpServers"]}
    assert (servers["github"]["command"], servers["github"]["args"]) == (
        "npx",
        ["-y", "@scope/server-github"],
    )
    assert servers["github"]["addedBy"] == "cli"
    assert servers["docs"]["description"] == "Docs"
    assert [skill["name"] for skill in listing["skills"]] == ["triage"]
    assert runner.invoke(app, ["rem", "skill", "path", *data]).output.strip() == str(
        tmp_path / "skills"
    )
    assert runner.invoke(app, ["rem", "mcp", "remove", "github", *data]).exit_code == 0


@pytest.fixture
def api(tmp_path: Path) -> Iterator[GoferUiServer]:
    server = GoferUiServer(("127.0.0.1", 0), tmp_path / "app", api_token="rem-library-test")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def call(server: GoferUiServer, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    connection = http.client.HTTPConnection(
        str(server.server_address[0]), int(server.server_address[1]), timeout=10
    )
    headers = {"Authorization": "Bearer rem-library-test", "Content-Type": "application/json"}
    connection.request(
        "POST" if body else "GET",
        "/api/rem-resources",
        body=json.dumps(body) if body else None,
        headers=headers,
    )
    response = connection.getresponse()
    payload = json.loads(response.read())
    connection.close()
    return response.status, payload


def test_ui_endpoint_reads_and_edits_the_library(api: GoferUiServer, tmp_path: Path) -> None:
    skill = write_skill(tmp_path / "deploy", "deploy", "Ship builds")
    status, payload = call(api)
    assert status == 200
    assert payload["skillsDir"] == str(api.data_dir / "skills")

    status, payload = call(api, {"action": "add_skill", "path": str(skill)})
    assert status == 200
    assert [entry["name"] for entry in payload["skills"]] == ["deploy"]
    server = {"name": "docs", "type": "http", "url": "https://example.com/mcp"}
    status, payload = call(api, {"action": "save_mcp", "server": server})
    assert status == 200
    assert payload["mcpServers"][0]["addedBy"] == "app"

    status, payload = call(api, {"action": "save_mcp", "server": {**server, "name": "bad name"}})
    assert status == 400
    assert "pattern" in payload["error"]
    status, payload = call(api, {"action": "save_mcp", "server": {**server, "name": "swarm"}})
    assert status == 400
    assert payload["error"] == "swarm is reserved for a built-in Raticode connection"
    status, payload = call(api, {"action": "remove_mcp", "name": "docs"})
    assert (status, payload["mcpServers"]) == (200, [])
