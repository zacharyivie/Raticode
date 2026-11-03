"""Rem task threads get the same skills, tools and MCP servers as desktop threads."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gofer.core.prompt_envelope import AgentResources, resource_cli_args, resource_index
from gofer.core.rem_library import with_library_resources
from gofer.ui.rem_home import RemHome
from tests.unit import test_rem_home
from tests.unit.test_rem_home import git, recipe, wait_run

REM_MANAGED = {"rem_artifacts"}


@pytest.fixture
def rem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """test_rem_home's RemHome with a fake provider."""
    yield from test_rem_home.home.__wrapped__(tmp_path, monkeypatch)  # type: ignore[attr-defined]


def skill(root: Path, name: str, description: str) -> dict[str, Any]:
    folder = root / "skills" / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\nBody\n")
    return {"path": str(folder), "enabled": True}


def thread_defaults(tmp_path: Path) -> dict[str, Any]:
    """Settings > Thread tools, as the desktop snapshots it for a new thread."""
    return {
        "shell": True,
        "web": True,
        "agents": True,
        "browser": False,
        "skills": [
            skill(tmp_path, "worktree-dev-flow", "Creates a git worktree off dev for each change."),
            skill(tmp_path, "unslop", "Cut AI tells from any writing."),
            {**skill(tmp_path, "off-skill", "Turned off."), "enabled": False},
        ],
        "mcpServers": [
            {"name": "notes", "type": "stdio", "command": "/bin/true", "args": ["--serve"]},
            {"name": "tracker", "type": "http", "url": "https://mcp.example.com/mcp"},
            {"name": "paused", "type": "http", "url": "https://x.example.com", "enabled": False},
        ],
    }


def capture(rem: RemHome) -> list[dict[str, Any]]:
    original = rem.source
    observed: list[dict[str, Any]] = []

    async def source(**kwargs: Any):
        observed.append(kwargs)
        async for event in original(**kwargs):
            yield event

    rem.source = source
    return observed


def provider_view(provider: str, workflow: dict[str, Any], data_dir: Path, cwd: Path) -> Any:
    """What the provider CLI receives for the selected tools, skills and MCP servers."""
    resolved = with_library_resources(workflow, data_dir) or {}
    selected = dict(resolved["remResources"])
    selected["mcpServers"] = [
        s for s in selected.get("mcpServers", []) if s["name"] not in REM_MANAGED
    ]
    resources = AgentResources.model_validate(selected)
    return {
        "args": resource_cli_args(provider, resources, cwd),
        "index": resource_index(resources),
    }


@pytest.mark.parametrize("provider", ["claude_code", "codex"])
def test_rem_writer_tasks_match_a_new_desktop_thread(rem: RemHome, tmp_path: Path, provider: str):
    defaults = thread_defaults(tmp_path)
    observed = capture(rem)
    rem.configure({"provider": provider, "resources": defaults})
    task = rem.create_task({"request_id": "create", **recipe(tmp_path, type="maintain")})["task"]
    assert wait_run(rem, task["id"], {"succeeded", "blocked"})["state"] == "succeeded"
    worker = observed[0]
    # App.jsx sends the thread's snapshot of the same defaults as remResources.
    desktop = {
        "projectRoot": str(tmp_path),
        "remResources": AgentResources.model_validate(defaults).model_dump(),
    }
    task_view = provider_view(provider, worker["workflow"], rem.data_dir, tmp_path)
    assert task_view == provider_view(provider, desktop, rem.data_dir, tmp_path)
    assert "worktree-dev-flow" in task_view["index"] and "unslop" in task_view["index"]
    assert "off-skill" not in task_view["index"]
    assert '"notes"' in task_view["index"] and '"tracker"' in task_view["index"]
    if provider == "codex":
        assert any(
            "skills.config=" in arg and "worktree-dev-flow" in arg for arg in task_view["args"]
        )
        assert any(arg.startswith("mcp_servers.notes=") for arg in task_view["args"])
    else:
        config = task_view["args"][task_view["args"].index("--mcp-config") + 1]
        assert '"notes"' in config and '"tracker"' in config and '"paused"' not in config
        assert "Bash" in task_view["args"][1] and "WebSearch" in task_view["args"][1]


@pytest.mark.parametrize("provider", ["claude_code", "codex"])
def test_rem_research_and_review_keep_skills_but_stay_read_only(
    rem: RemHome, tmp_path: Path, provider: str
):
    defaults = thread_defaults(tmp_path)
    observed = capture(rem)
    rem.configure({"provider": provider, "resources": defaults})
    # Every review is read-only; a maintain task reaches review without a saved report.
    task = rem.create_task({"request_id": "maintain", **recipe(tmp_path, type="maintain")})
    assert wait_run(rem, task["task"]["id"], {"succeeded", "blocked"})["state"] == "succeeded"
    reviewer, maintained = observed[-1], len(observed)
    task = rem.create_task({"request_id": "research", **recipe(tmp_path, type="research")})
    wait_run(rem, task["task"]["id"], {"succeeded", "blocked", "failed"})
    worker = observed[maintained]
    enabled = [s for s in defaults["skills"] if s["enabled"]]
    assert "independent read-only reviewer" in reviewer["agent_instructions"]
    for options, web in ((worker, True), (reviewer, False)):
        resources = options["workflow"]["remResources"]
        assert options["workflow"]["remManagedTask"]["readOnly"] is True
        assert resources["skills"] == enabled
        assert resources["web"] is web
        assert resources["agents"] is False and resources["browser"] is False
        # User MCP servers can change things elsewhere; only Rem's own tool remains.
        assert {s["name"] for s in resources.get("mcpServers", [])} <= REM_MANAGED
        index = resource_index(AgentResources.model_validate(resources))
        assert "worktree-dev-flow" in index
    if provider == "codex":
        assert reviewer["permission_mode"] == "read-only"


def test_rem_tasks_use_the_users_browser_and_swarm_settings(rem: RemHome, tmp_path: Path):
    observed = capture(rem)
    rem.configure({"provider": "codex", "browser_target": "external", "swarm_access": False})
    task = rem.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    wait_run(rem, task["id"], {"succeeded", "blocked"})
    for options in observed:
        assert options["workflow"]["remBrowser"] == {"target": "external"}
        assert options["workflow"]["remSwarmAccess"]["enabled"] is False
    with pytest.raises(ValueError):
        rem.configure({"provider": "codex", "browser_target": "anywhere"})
    with pytest.raises(ValueError):
        rem.configure({"provider": "codex", "swarm_access": "yes"})


def test_a_worktree_skill_replaces_rems_temporary_worktree(
    rem: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project = tmp_path / "repo"
    project.mkdir()
    git(project, "init", "-q", "-b", "main")
    (project / "README.md").write_text("hello\n")
    git(project, "add", "-A")
    git(project, "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-qm", "init")
    for key in ("GIT_AUTHOR", "GIT_COMMITTER"):
        monkeypatch.setenv(f"{key}_NAME", "Rem")
        monkeypatch.setenv(f"{key}_EMAIL", "rem@example.com")
    rem.configure({"provider": "claude_code", "resources": thread_defaults(tmp_path)})
    workers: list[dict[str, Any]] = []
    release = threading.Event()
    original = rem.source

    async def source(**kwargs: Any):
        if "independent read-only reviewer" not in kwargs["agent_instructions"]:
            workers.append(kwargs)
            while not release.is_set() and not kwargs["cancel_event"].is_set():
                await asyncio.sleep(0.01)
        async for event in original(**kwargs):
            yield event

    rem.source = source
    first = rem.create_task({"request_id": "first", **recipe(project)})["task"]
    second = rem.create_task({"request_id": "second", **recipe(project)})["task"]
    try:
        rem.tick()
        rem.tick()
        deadline = time.monotonic() + 5
        while len(workers) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(workers) == 2
    finally:
        release.set()
    for task in (first, second):
        run = wait_run(rem, task["id"], {"succeeded", "blocked"})
        assert run["state"] == "succeeded", run
        assert not run.get("worktree_note")
    for options in workers:
        assert options["workflow"]["projectRoot"] == str(project)
        instructions = options["agent_instructions"]
        assert "temporary git worktree" not in instructions
        assert "The enabled worktree-dev-flow skill" in instructions
        assert "keep working in the worktree by absolute path" in instructions
    assert "rem/" not in git(project, "branch")
    assert git(project, "worktree", "list").count("\n") == 0


def test_rem_omits_worktree_guidance_without_a_worktree_skill(rem: RemHome, tmp_path: Path):
    resources = thread_defaults(tmp_path)
    resources["skills"] = [s for s in resources["skills"] if "worktree" not in s["path"]]
    observed = capture(rem)
    rem.configure({"provider": "codex", "resources": resources})
    task = rem.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    wait_run(rem, task["id"], {"succeeded", "blocked"})
    assert "skill sets how changes" not in observed[0]["agent_instructions"]


def test_rem_task_source_adds_read_only_fleet_status():
    from gofer.ui.server import GoferUiServer
    from tests.unit.test_swarm_tools import request

    tools: list[str] = []

    async def device_source(**kwargs: Any):
        servers = kwargs["workflow"]["remResources"]["mcpServers"]
        fleet = next(s for s in servers if s["name"] == "fleet")
        listed = request(fleet["url"], {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools.extend(t["name"] for t in listed[1]["result"]["tools"])
        yield {"type": "final", "message": {"body": "ok"}}

    server = SimpleNamespace(devices=SimpleNamespace(action=lambda body: {}))
    server._device_chat_source = device_source

    async def run() -> list[dict[str, Any]]:
        return [
            event
            async for event in GoferUiServer._rem_task_source(
                server,  # type: ignore[arg-type]
                permission_mode="workspace-write",
                workflow={"remResources": {"mcpServers": []}},
            )
        ]

    assert asyncio.run(run())[-1]["type"] == "final"
    assert "fleet_status" in tools
    # Sending work to another desktop would delegate the task again.
    assert "fleet_send" not in tools
