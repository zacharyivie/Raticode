"""A brain with Allow cloud processing off never reaches a cloud provider through a project.

A project that is or sits inside such a brain is refused. A project that holds one runs
only in Claude Code's sandbox, which hides the brain from the shell and every file tool.
"""

from __future__ import annotations

import itertools
import json
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gofer.core.provider_profiles import ProviderProfile, save_provider_profiles
from gofer.ui import chat, claude_sandbox
from gofer.ui.brain_library import BrainLibrary
from gofer.ui.brain_privacy import PrivateBrainError, turn_private_brains
from gofer.ui.chat import ChatProviderError, stream_workflow_chat
from tests.unit import test_ui_server as harness

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="Claude Code has no sandbox on Windows"
)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "data"
    folder.mkdir()
    return folder


@pytest.fixture
def library(data_dir: Path) -> Iterator[BrainLibrary]:
    service = BrainLibrary(data_dir, start=False)
    yield service
    service.close()


def register(library: BrainLibrary, root: Path, *, cloud: bool) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "secret.md").write_text("# Private\n\nOnly on this computer.\n", encoding="utf-8")
    brain = library.open({"root": str(root), "name": root.name})
    return set_cloud(library, brain, cloud)


def set_cloud(library: BrainLibrary, brain: dict[str, Any], cloud: bool) -> dict[str, Any]:
    with library.connect() as db:
        body: dict[str, Any] = json.loads(
            db.execute("SELECT body FROM brains WHERE id=?", (brain["id"],)).fetchone()[0]
        )
    body["settings"]["allowCloud"] = cloud
    library.put_brain(body)
    return body


def rem_config(*roots: Path, memory: Path | None = None) -> dict[str, Any]:
    config: dict[str, Any] = {
        "projects": [{"root": str(root), "name": root.name} for root in roots],
    }
    if memory is not None:
        config["brain"] = {"enabled": True, "root": str(memory)}
    return config


def authorizing_server(data_dir: Path) -> Any:
    server = harness._fake_server(data_dir)
    # Path grants are another check; these tests only look at brains.
    setattr(server, "_authorize_swarm_workspace", lambda *_args: None)
    return server


def close(server: Any) -> None:
    server.chat_jobs.close()
    server.chat_steering.close()
    server.generation_jobs.close()


def test_a_project_that_is_a_private_brain_is_refused(library, data_dir, tmp_path) -> None:
    brain = tmp_path / "Journal"
    register(library, brain, cloud=False)
    inside = brain / "drafts"
    inside.mkdir()
    server = authorizing_server(data_dir)
    try:
        for project in (brain, inside):
            recipe = {"type": "develop", "project_dir": str(project), "provider": "claude_code"}
            with pytest.raises(PrivateBrainError) as refused:
                server._authorize_rem_home(rem_config(project), recipe)
            message = str(refused.value)
            assert "Journal" in message
            assert "Allow cloud processing" in message and "localhost" in message
            # Rem's window context drops the project with the same reason.
            reasons = server._private_rem_projects({}, {}, [str(project)])
            assert reasons[str(project)] == message
    finally:
        close(server)


def test_another_open_project_that_is_private_does_not_stop_a_task(
    library, data_dir, tmp_path
) -> None:
    brain = tmp_path / "Journal"
    register(library, brain, cloud=False)
    project = tmp_path / "app"
    project.mkdir()
    server = authorizing_server(data_dir)
    try:
        server._authorize_rem_home(
            rem_config(project, brain), {"type": "develop", "project_dir": str(project)}
        )
    finally:
        close(server)


class _Store:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    @contextmanager
    def connect(self) -> Iterator[None]:
        yield None

    def setting(self, _db: None, name: str) -> dict[str, Any]:
        assert name == "config"
        return self.config


def test_one_project_turned_private_leaves_rems_context_with_the_other(
    library, data_dir, tmp_path
) -> None:
    journal = tmp_path / "Journal"
    brain = register(library, journal, cloud=True)
    app = tmp_path / "app"
    app.mkdir()
    server = authorizing_server(data_dir)
    # The saved config still lists the brain: the window only posts its context again
    # when the open projects change.
    setattr(server, "rem_home", SimpleNamespace(store=_Store(rem_config(app, journal))))
    setattr(
        server,
        "organizations",
        SimpleNamespace(store=SimpleNamespace(summaries=lambda: [])),
    )
    setattr(server, "swarms", SimpleNamespace(list=lambda root: [{"projectRoot": root}]))
    try:
        seen = {s["projectRoot"] for s in server._rem_home_context()["swarms"]}
        assert seen == {str(app), str(journal)}
        set_cloud(library, brain, False)
        seen = {s["projectRoot"] for s in server._rem_home_context()["swarms"]}
        assert seen == {str(app)}
        # Rem's chat and reviews of tasks elsewhere keep running; a task in it stops.
        server._authorize_rem_home(rem_config(app, journal), {})
        server._authorize_rem_home(
            rem_config(app, journal), {"type": "develop", "project_dir": str(app)}
        )
        with pytest.raises(PrivateBrainError, match="Journal"):
            server._authorize_rem_home(
                rem_config(app, journal), {"type": "develop", "project_dir": str(journal)}
            )
    finally:
        close(server)


def test_the_memory_folder_still_works(library, data_dir, tmp_path) -> None:
    memory = tmp_path / "Memory"
    register(library, memory, cloud=False)
    server = authorizing_server(data_dir)
    try:
        recipe = {"type": "develop", "project_dir": str(memory), "provider": "claude_code"}
        server._authorize_rem_home(rem_config(memory, memory=memory), recipe)
        server._authorize_rem_home(rem_config(memory, memory=memory), {})
        assert (
            server._private_rem_projects(rem_config(memory, memory=memory), {}, [str(memory)]) == {}
        )
    finally:
        close(server)
    # A turn given the folder as its Second Brain reads it, and so does a reviewer
    # without one, through Rem's saved Settings > Memory folder.
    assert (
        turn_private_brains(
            [memory], provider={"id": "codex"}, data_dir=data_dir, approved=[str(memory)]
        )
        == []
    )
    store = sqlite3.connect(data_dir / "rem-home.sqlite3")
    store.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, body TEXT NOT NULL)")
    store.execute(
        "INSERT INTO settings VALUES('config', ?)",
        (json.dumps({"brain": {"enabled": True, "root": str(memory)}}),),
    )
    store.commit()
    store.close()
    assert turn_private_brains([memory.parent], provider={"id": "codex"}, data_dir=data_dir) == []


def test_a_localhost_provider_or_a_brain_allowing_cloud_is_unchanged(
    library, data_dir, tmp_path
) -> None:
    private = tmp_path / "Journal"
    register(library, private, cloud=False)
    shared = tmp_path / "Research"
    register(library, shared, cloud=True)
    save_provider_profiles(
        {
            "ollama": ProviderProfile(
                name="ollama",
                subscription="openai_api",
                model="llama3",
                api_base_url="http://127.0.0.1:11434/v1",
            )
        },
        data_dir,
    )
    local = {"id": "openai_api", "profile": "ollama"}
    assert turn_private_brains([private, tmp_path], provider=local, data_dir=data_dir) == []
    assert turn_private_brains([shared], provider={"id": "codex"}, data_dir=data_dir) == []
    hidden = turn_private_brains([tmp_path], provider={"id": "codex"}, data_dir=data_dir)
    assert hidden == [("Journal", private.resolve())]
    server = authorizing_server(data_dir)
    try:
        recipe = {"type": "develop", "project_dir": str(shared), "provider": "claude_code"}
        server._authorize_rem_home(rem_config(shared), recipe)
    finally:
        close(server)


def test_hidden_folders_get_deny_read_and_file_tool_rules(tmp_path: Path) -> None:
    project = tmp_path / "project"
    brain = project / "journal"
    brain.mkdir(parents=True)
    plain = claude_sandbox.sandbox_settings(
        root=project, data_dir=tmp_path / "data", read_only=False
    )
    settings = claude_sandbox.sandbox_settings(
        root=project, data_dir=tmp_path / "data", read_only=False, hidden=[brain]
    )
    files = settings["sandbox"]["filesystem"]
    assert str(brain.resolve()) in files["denyRead"]
    assert str(brain.resolve()) in files["denyWrite"]
    rules = settings["permissions"]["deny"]
    for tool in ("Read", "Edit", "Grep", "Glob"):
        assert f"{tool}(/{brain.resolve()}/**)" in rules
    assert len(rules) == len(plain["permissions"]["deny"]) + 8
    # A folder the turn must use inside the brain would show it again.
    with pytest.raises(ValueError, match="can't hide"):
        claude_sandbox.sandbox_settings(
            root=project,
            data_dir=tmp_path / "data",
            read_only=False,
            writable=[brain / "scratch"],
            hidden=[brain],
        )


_threads = itertools.count()


async def _turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    project: Path,
    *,
    sandboxed: bool,
) -> dict[str, Any]:
    """One Claude Code turn in ``project`` through a fake CLI that records its argv."""
    monkeypatch.setattr(claude_sandbox, "unavailable_reason", lambda: None)
    report = tmp_path / "report.json"
    script = tmp_path / "fake_claude.py"
    script.write_text(
        "import json, sys\n"
        f"open({str(report)!r}, 'w').write(json.dumps(sys.argv))\n"
        "print(json.dumps({'type':'result','session_id':'claude-1','result':'done'}))\n"
    )
    gof = tmp_path / "trusted" / "gof"
    gof.parent.mkdir(exist_ok=True)
    gof.write_text("#!/bin/sh\n")
    gof.chmod(0o755)

    async def no_compaction(**options: Any) -> tuple[Any, bool]:
        return options["messages"], False

    real_build_command = chat._build_chat_command
    monkeypatch.setattr(chat, "_compact_chat_messages_if_needed", no_compaction)
    monkeypatch.setattr(chat, "resolve_provider_executable", lambda _: sys.executable)
    monkeypatch.setattr(chat, "ensure_local_gofer_cli", lambda _data_dir: gof)
    monkeypatch.setattr(
        chat,
        "_build_chat_command",
        lambda **options: [sys.executable, str(script), *real_build_command(**options)[1:]],
    )
    report.unlink(missing_ok=True)
    managed = {"readOnly": False, "runId": "run", **({"claudeSandbox": True} if sandboxed else {})}
    events = [
        event
        async for event in stream_workflow_chat(
            provider="claude_code",
            model="cli-default",
            messages=[{"role": "user", "body": "tidy the notes"}],
            workflow={
                "projectRoot": str(project),
                # A new thread each time, so a turn is never a resent message.
                "chatThreadId": f"thread-{next(_threads)}",
                "remResources": {},
                "remManagedTask": managed,
            },
            working_dir=project,
            data_dir=tmp_path / "data",
            permission_mode="dontAsk",
            trusted_scratch_dir=tmp_path / "data" / "rem-work" / "turn-scratch" / "run",
        )
    ]
    assert events[-1]["type"] == "final", events[-1]
    argv: list[str] = json.loads(report.read_text())
    settings: dict[str, Any] = json.loads(argv[argv.index("--settings") + 1])
    return settings


@pytest.mark.asyncio
async def test_a_project_holding_a_private_brain_hides_it_from_the_sandboxed_turn(
    monkeypatch, library, tmp_path
) -> None:
    project = tmp_path / "project"
    brain = project / "journal"
    register(library, brain, cloud=False)
    settings = await _turn(monkeypatch, tmp_path, project, sandboxed=True)
    hidden = str(brain.resolve())
    assert hidden in settings["sandbox"]["filesystem"]["denyRead"]
    assert hidden in settings["sandbox"]["filesystem"]["denyWrite"]
    for tool in ("Read", "Edit", "Grep", "Glob"):
        assert f"{tool}(/{hidden}/**)" in settings["permissions"]["deny"]
    # A turn that can't be sandboxed has no way to hide it.
    with pytest.raises(ChatProviderError, match="journal.*Allow cloud processing.*localhost"):
        await _turn(monkeypatch, tmp_path, project, sandboxed=False)


@pytest.mark.asyncio
async def test_turning_cloud_processing_off_refuses_the_next_turn(
    monkeypatch, library, data_dir, tmp_path
) -> None:
    project = tmp_path / "Journal"
    brain = register(library, project, cloud=True)
    settings = await _turn(monkeypatch, tmp_path, project, sandboxed=True)
    assert str(project.resolve()) not in settings["sandbox"]["filesystem"]["denyRead"]
    assert "Read(/" + str(project.resolve()) + "/**)" not in settings["permissions"]["deny"]
    set_cloud(library, brain, False)
    with pytest.raises(ChatProviderError, match="is the Second Brain Journal"):
        await _turn(monkeypatch, tmp_path, project, sandboxed=True)
    # A running Rem task stops the same way at its next check.
    server = authorizing_server(data_dir)
    try:
        with pytest.raises(PrivateBrainError, match="Journal"):
            server._authorize_rem_home(
                rem_config(project), {"type": "develop", "project_dir": str(project)}
            )
    finally:
        close(server)


@pytest.mark.parametrize("provider", ["codex", "claude_code"])
def test_a_one_shot_chat_request_refuses_a_project_holding_a_private_brain(
    monkeypatch, library, data_dir, tmp_path, provider
) -> None:
    # POST /api/chat with no conversationId used to run its own copy of the provider
    # setup, which never looked for private brains and started the CLI in the project.
    project = tmp_path / "project"
    register(library, project / "journal", cloud=False)
    started: list[list[str]] = []

    async def must_not_start(command: list[str], **_kwargs: Any):
        started.append(command)
        yield {"type": "chunk", "stream": "stdout", "text": "done", "returncode": None}
        yield {"type": "exit", "stream": None, "text": "", "returncode": 0}

    async def old_copy(command: list[str], **_kwargs: Any) -> tuple[int, str, str]:
        started.append(command)
        return 0, "done", ""

    monkeypatch.setattr(chat, "resolve_provider_executable", lambda _: sys.executable)
    monkeypatch.setattr(chat, "stream_subprocess", must_not_start)
    monkeypatch.setattr(chat, "run_subprocess", old_copy)
    result = harness._request(
        data_dir,
        "POST",
        "/api/chat",
        body={
            "provider": provider,
            "model": "cli-default",
            "messages": [{"role": "user", "body": "tidy the notes"}],
            "workflow": {"projectRoot": str(project)},
        },
    )
    assert result.status == 400, result.text()
    assert "journal" in result.text() and "Allow cloud processing" in result.text()
    assert started == []
