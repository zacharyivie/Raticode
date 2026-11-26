"""Turns whose Second Brain names only a folder search the library brain registered there.

Organizations and device-hosted threads send the Settings > Memory folder by path. When
a library brain is registered at that folder, the run gets its brain ID so Rem searches
the library's extracted passages. Unregistered folders keep the folder's own index,
which now stores extracted text rather than raw bytes.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sqlite3
import time
from collections.abc import AsyncIterator
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gofer.cli.commands.second_brain import fast_main
from gofer.ui import second_brain
from gofer.ui import server as server_module
from gofer.ui.brain_library import BrainLibrary
from gofer.ui.device_context import shared_context
from gofer.ui.organization_store import OrganizationConfig
from gofer.ui.organizations import second_brain_widened
from gofer.ui.second_brain import SecondBrain, library_brain_config, with_second_brain
from gofer.ui.server import DesktopPathGrantStore, GoferUiRequestHandler, GoferUiServer
from tests.helpers.brain_library import wait_idle

CLI = Path("/trusted/Raticode")
SERVE_STDIO = second_brain.serve_stdio


@pytest.fixture
def setup(tmp_path: Path):
    data = (tmp_path / "data").resolve()
    data.mkdir()
    vault = (tmp_path / "vault").resolve()
    (vault / "Notes").mkdir(parents=True)
    (vault / "Notes" / "colors.md").write_text(
        "# Palette\n\nThe brand color is a deep teal for every chart.\n", encoding="utf-8"
    )
    library = BrainLibrary(data, start=False)
    brain = library.open({"root": str(vault)})
    wait_idle(library)
    # The Settings > Memory folder was never given Allow cloud processing.
    assert library.brain(brain["id"])["settings"]["allowCloud"] is False
    server = GoferUiServer.__new__(GoferUiServer)
    server.data_dir = data
    server.second_brains = library
    server.path_grants = DesktopPathGrantStore()
    handler = GoferUiRequestHandler.__new__(GoferUiRequestHandler)
    handler.server = server
    handler.headers = Message()
    grant = server.path_grants.register(vault, "memory-grant")
    yield SimpleNamespace(
        data=data,
        vault=vault,
        library=library,
        brain=brain,
        server=server,
        handler=handler,
        grant=grant,
    )
    library.close()


def mcp_args(workflow: dict[str, Any], data_dir: Path | None = None) -> list[str]:
    configured = with_second_brain(workflow, CLI, {"threadId": "t1"}, data_dir)
    assert configured is not None
    servers = configured["remResources"]["mcpServers"]
    return list(next(item for item in servers if item["name"] == "second_brain")["args"])


def assert_library_args(args: list[str], data: Path, brain_id: str) -> None:
    assert args[args.index("--library-data-dir") + 1] == str(data)
    assert args[args.index("--brain-id") + 1] == brain_id
    assert "--memory-folder" in args


def test_root_only_org_config_runs_with_the_library_brain(setup, monkeypatch) -> None:
    folder = {"enabled": True, "root": str(setup.vault), "format": "md"}
    config = {"remSecondBrain": dict(folder)}
    # Saving the organization keeps the folder exactly as entered.
    entered = {**folder, "grantId": setup.grant}
    setup.handler._validate_second_brain({"workflow": {"remSecondBrain": entered}}, fill=False)
    assert entered == {**folder, "grantId": setup.grant}
    assert second_brain_widened(config, config) == ""

    project = setup.vault.parent / "project"
    project.mkdir()
    seen: list[dict[str, Any]] = []

    @contextlib.contextmanager
    def rem_session(*_args: Any, **_kwargs: Any):
        yield "http://127.0.0.1:1/swarm"

    async def capture(_source: Any, **options: Any):
        seen.append(options["workflow"])
        if False:
            yield {}

    setup.server.swarms = SimpleNamespace(rem_session=rem_session)
    setup.server.resource_limits = None
    monkeypatch.setattr("gofer.ui.rem_threads.stream_with_thread_tools", capture)
    stored = config["remSecondBrain"]
    workflow = {
        "projectRoot": str(project),
        "organizationWorkspacePaths": [str(project)],
        "remSecondBrain": stored,
        "remResources": {},
    }

    async def run() -> None:
        async for _ in setup.server._employee_chat_source(workflow=workflow):
            pass

    asyncio.run(run())
    run_brain = seen[0]["remSecondBrain"]
    assert run_brain["brainIds"] == [setup.brain["id"]]
    assert_library_args(mcp_args(seen[0]), setup.data, setup.brain["id"])
    # Filled for the run only: the stored configuration still names the folder alone.
    assert stored == folder
    assert second_brain_widened(config, {"remSecondBrain": stored}) == ""
    # The organization model never stores the run-time mark.
    saved = OrganizationConfig.model_validate(
        {"name": "Org", "remSecondBrain": {**folder, "memoryFolder": True}}
    )
    assert "memoryFolder" not in saved.remSecondBrain


def test_root_only_device_workflow_gets_library_args(setup, monkeypatch) -> None:
    # deviceThreadHosting.js / deviceWorkspaceSync.js send the Memory folder by path.
    workflow = {
        "remSecondBrain": {
            "enabled": True,
            "root": str(setup.vault),
            "format": "html",
            "grantId": setup.grant,
        }
    }
    setup.handler._validate_second_brain({"workflow": workflow})
    brain = workflow["remSecondBrain"]
    assert brain["brainIds"] == [setup.brain["id"]]
    assert brain["grantId"] == setup.grant and brain["root"] == str(setup.vault)
    assert brain["libraryDataDir"] == str(setup.data)
    context = shared_context({"provider": "codex"}, workflow, [], None)
    assert_library_args(mcp_args(context["workflow"]), setup.data, setup.brain["id"])

    # A stored hosted context is derived again from the folder when its turn runs.
    seen: list[dict[str, Any]] = []

    class Organizations:
        def rem_stream(self, _source: Any, **options: Any):
            seen.append(options["workflow"])

            async def empty() -> AsyncIterator[dict[str, Any]]:
                if False:
                    yield {}

            return empty()

    setup.server.organizations = Organizations()
    forged = {
        "remSecondBrain": {
            "enabled": True,
            "root": str(setup.vault),
            "brainIds": ["brain-somebody-else"],
            "libraryDataDir": "/elsewhere",
            "memoryFolder": True,
        }
    }

    async def run() -> None:
        async for _ in setup.server._device_chat_source(workflow=forged):
            pass

    asyncio.run(run())
    assert seen[0]["remSecondBrain"]["brainIds"] == [setup.brain["id"]]
    assert seen[0]["remSecondBrain"]["libraryDataDir"] == str(setup.data)


def test_library_search_returns_citation_fields_for_the_memory_folder(setup, monkeypatch) -> None:
    ran: list[bool] = []

    def fake(call, _definitions, _rules, _name):
        found = call("search", {"format": "json", "query": "color"})["results"]
        assert len(found) == 1
        top = found[0]
        assert top["documentId"] and top["version"] and top["status"] == "current"
        assert top["passages"][0]["blockId"]
        note = call("read_note", {"documentId": top["documentId"]})
        assert note["version"] == top["version"]
        ran.append(True)

    monkeypatch.setattr(second_brain, "serve_stdio", fake)
    second_brain.serve_registered_brains(
        setup.data, [setup.brain["id"]], "md", "auto", memory_folder=setup.vault
    )
    assert ran == [True]

    # Without the approved-folder flag, the brain still needs cloud permission.
    def refuse(call, _definitions, _rules, _name):
        with pytest.raises(ValueError, match="revoked"):
            call("search", {"format": "json", "query": "color"})
        ran.append(False)

    monkeypatch.setattr(second_brain, "serve_stdio", refuse)
    with pytest.raises(ValueError, match="revoked"):
        second_brain.serve_registered_brains(setup.data, [setup.brain["id"]], "md", "auto")


def test_memory_folder_does_not_need_allow_cloud_but_explicit_brains_do(setup) -> None:
    folder = {
        "workflow": {
            "remSecondBrain": {"enabled": True, "root": str(setup.vault), "grantId": setup.grant}
        }
    }
    setup.handler._validate_second_brain(folder)
    assert folder["workflow"]["remSecondBrain"]["memoryFolder"] is True
    explicit = {"workflow": {"remSecondBrain": {"enabled": True, "brainIds": [setup.brain["id"]]}}}
    with pytest.raises(ValueError, match="Allow cloud processing"):
        setup.handler._validate_second_brain(explicit)
    # A client can't mark a brain as the approved folder: the mark and its brain list
    # are dropped, leaving no folder to use.
    forged = {
        "workflow": {
            "remSecondBrain": {
                "enabled": True,
                "brainIds": [setup.brain["id"]],
                "memoryFolder": True,
            }
        }
    }
    with pytest.raises(ValueError, match="Choose an existing"):
        setup.handler._validate_second_brain(forged)


def test_unregistered_folder_falls_back_to_the_folder_index(setup, tmp_path) -> None:
    other = (tmp_path / "other").resolve()
    other.mkdir()
    (other / "note.md").write_text("Plain note about tides.\n", encoding="utf-8")
    grant = setup.server.path_grants.register(other, "other-grant")
    workflow = {"remSecondBrain": {"enabled": True, "root": str(other), "grantId": grant}}
    setup.handler._validate_second_brain({"workflow": workflow})
    assert "brainIds" not in workflow["remSecondBrain"]
    assert "memoryFolder" not in workflow["remSecondBrain"]
    args = mcp_args(workflow)
    assert "--library-data-dir" not in args and "--memory-folder" not in args
    assert [hit["path"] for hit in SecondBrain(other).search("tides")] == ["note.md"]
    assert (
        library_brain_config(workflow["remSecondBrain"], [], setup.data)
        == workflow["remSecondBrain"]
    )


def serve_folder_turn(
    monkeypatch: pytest.MonkeyPatch, args: list[str], calls: list[tuple[str, dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Run the turn's second_brain server from its arguments and return each tool result."""
    requests = "".join(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": number,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        + "\n"
        for number, (name, arguments) in enumerate(calls)
    )
    output = io.StringIO()

    def captured(*serve_args: Any, **_: Any) -> None:
        SERVE_STDIO(*serve_args, input_stream=io.StringIO(requests), output_stream=output)

    monkeypatch.setattr(second_brain, "serve_stdio", captured)
    assert args[:2] == ["ui", "second-brain"]
    assert fast_main(args[2:]) == 0
    return [json.loads(line)["result"] for line in output.getvalue().splitlines()]


def test_memory_folder_index_leaves_out_private_brains_inside_it(
    setup, tmp_path, monkeypatch
) -> None:
    memory = (tmp_path / "memory").resolve()
    (memory / "Private").mkdir(parents=True)
    (memory / "open.md").write_text("Open note about lighthouses.\n", encoding="utf-8")
    secret = memory / "Private" / "secret.md"
    secret.write_text("Private note about lighthouses and the vault code.\n", encoding="utf-8")
    workflow = {"remSecondBrain": {"enabled": True, "root": str(memory)}}
    calls = [
        ("search", {"query": "lighthouses"}),
        ("read_note", {"path": "Private/secret.md"}),
    ]

    # Before Private is a brain, the folder index holds its note.
    args = mcp_args(workflow, setup.data)
    assert "--library-data-dir" not in args
    found, read = serve_folder_turn(monkeypatch, args, calls)
    assert {hit["path"] for hit in json.loads(found["content"][0]["text"])} == {
        "open.md",
        "Private/secret.md",
    }
    assert "vault code" in read["content"][0]["text"]

    # Registered with Allow cloud processing off, it is left out at the next turn.
    inner = setup.library.open({"root": str(memory / "Private")})
    wait_idle(setup.library)
    assert setup.library.brain(inner["id"])["settings"]["allowCloud"] is False
    args = mcp_args(workflow, setup.data)
    assert args[args.index("--private-brain-folder") + 1] == str(memory / "Private")
    found, read = serve_folder_turn(monkeypatch, args, calls)
    assert [hit["path"] for hit in json.loads(found["content"][0]["text"])] == ["open.md"]
    assert read["isError"] is True
    assert "Allow cloud processing" in read["content"][0]["text"]
    assert "vault code" not in read["content"][0]["text"]
    # The row stored before registration is gone from the folder's index, too.
    database = memory / ".raticode" / "second-brain.sqlite3"
    with contextlib.closing(sqlite3.connect(database)) as connection:
        paths = [row[0] for row in connection.execute("SELECT path FROM notes")]
    assert paths == ["open.md"]

    # With Allow cloud processing on, the brain's notes are the folder's again.
    setup.library.update_brain({"id": inner["id"], "patch": {"settings": {"allowCloud": True}}})
    args = mcp_args(workflow, setup.data)
    assert "--private-brain-folder" not in args
    found, _read = serve_folder_turn(monkeypatch, args, calls)
    assert "Private/secret.md" in {hit["path"] for hit in json.loads(found["content"][0]["text"])}


def test_fallback_search_reads_extracted_text_not_css(tmp_path: Path) -> None:
    root = (tmp_path / "plain").resolve()
    root.mkdir()
    (root / "styled.html").write_text(
        "<html><head><style>body { color: red; background-color: #fff }</style>"
        "<script>var color = 1;</script></head>"
        "<body><h1>Harbor</h1><p>Boats leave at dawn.</p></body></html>",
        encoding="utf-8",
    )
    (root / "palette.md").write_text("---\ntags: [color]\n---\nWarm tones only.\n")
    brain = SecondBrain(root)
    assert brain.search("color") == []
    assert [hit["path"] for hit in brain.search("dawn")] == ["styled.html"]


def test_old_raw_byte_index_is_rebuilt(tmp_path: Path) -> None:
    root = (tmp_path / "plain").resolve()
    root.mkdir()
    page = root / "styled.html"
    page.write_text("<style>p { color: red }</style><p>Quiet harbor.</p>", encoding="utf-8")
    database = root / ".raticode" / "second-brain.sqlite3"
    database.parent.mkdir()
    info = page.stat()
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE VIRTUAL TABLE notes USING fts5(id UNINDEXED, path, content)")
        connection.execute(
            "CREATE TABLE note_state (id TEXT PRIMARY KEY, mtime_ns INTEGER, size INTEGER)"
        )
        connection.execute("INSERT INTO notes VALUES ('x', 'styled.html', ?)", (page.read_text(),))
        connection.execute(
            "INSERT INTO note_state VALUES ('x', ?, ?)", (info.st_mtime_ns, info.st_size)
        )
        connection.commit()
    assert SecondBrain(root).search("color") == []
    assert [hit["path"] for hit in SecondBrain(root).search("harbor")] == ["styled.html"]


def test_folder_index_is_deleted_once_the_library_serves_the_folder(setup, tmp_path) -> None:
    # The old server wrote this; it is stale once the library serves the folder.
    SecondBrain(setup.vault).search("teal")
    bookkeeping = setup.vault / ".raticode"
    assert (bookkeeping / "second-brain.sqlite3").is_file()
    keep = bookkeeping / "other.json"
    keep.write_text("{}")
    filled = server_module.library_brain(setup.server, {"enabled": True, "root": str(setup.vault)})
    assert filled["brainIds"] == [setup.brain["id"]]
    assert not any(bookkeeping.glob("second-brain.sqlite3*"))
    assert keep.is_file()
    # The index worker was stopped, so nothing recreates it.
    time.sleep(0.1)
    assert not (bookkeeping / "second-brain.sqlite3").exists()

    other = (tmp_path / "other").resolve()
    other.mkdir()
    (other / "n.md").write_text("tide\n")
    SecondBrain(other).search("tide")
    server_module.library_brain(setup.server, {"enabled": True, "root": str(other)})
    assert (other / ".raticode" / "second-brain.sqlite3").is_file()


def test_rem_home_context_searches_the_library_brain(setup, monkeypatch) -> None:
    config = {"brain": {"enabled": True, "root": str(setup.vault)}, "projects": []}

    @contextlib.contextmanager
    def connect():
        yield None

    store = SimpleNamespace(
        connect=connect,
        setting=lambda _db, _key: config,
        tail=lambda _n, roles=(): [{"body": "What color are the charts?"}],
    )
    setup.server.rem_home = SimpleNamespace(store=store)
    setup.server.organizations = SimpleNamespace(store=SimpleNamespace(summaries=lambda: []))
    setup.server.swarms = SimpleNamespace(list=lambda _root: [])
    setup.server.chat_jobs = SimpleNamespace(active_snapshot=lambda: [])
    monkeypatch.setattr(GoferUiServer, "_authorize_rem_home", lambda *_a: None)
    monkeypatch.setattr(server_module, "list_workflow_payloads", lambda _d: {"workflows": []})
    searched: list[dict[str, Any]] = []
    real = setup.library.search

    def spy(params: dict[str, Any]) -> dict[str, Any]:
        searched.append(params)
        found: dict[str, Any] = real(params)
        return found

    monkeypatch.setattr(setup.library, "search", spy)
    context = setup.server._rem_home_context("What color are the charts?")
    assert searched and searched[0]["brainIds"] == [setup.brain["id"]]
    assert [Path(note["path"]).name for note in context["knowledge"]] == ["colors.md"]
    # Rem's saved settings are not changed by the lookup.
    assert config["brain"] == {"enabled": True, "root": str(setup.vault)}
