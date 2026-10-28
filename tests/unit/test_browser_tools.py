from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import stat
import threading
from collections.abc import AsyncGenerator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.ui.browser_tools import (
    ADMIN_TOKEN_ENV,
    GRANTS_DIR,
    SERVICE_URL_ENV,
    TOOLS,
    BrowserTools,
    browser_owner,
    browser_target,
    desktop_browser_service,
    read_capability,
    releases_browser_access,
    with_agent_browser,
)
from gofer.ui.second_brain import ToolContent, serve_stdio
from gofer.utils.process import build_subprocess_env

ADMIN = "a" * 64
CAPABILITY = "c" * 64


class _Desktop:
    """A stand-in for the desktop app's browser service."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.reply: tuple[int, dict[str, Any]] = (200, {})
        self.url = ""

    def admin(self, action: str) -> list[dict[str, Any]]:
        return [
            body
            for path, _token, body in self.requests
            if path == "/browser/admin" and body["action"] == action
        ]


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Desktop]:
    state = _Desktop()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server API
            length = int(self.headers["Content-Length"])
            body = json.loads(self.rfile.read(length))
            state.requests.append((self.path, self.headers["Authorization"], body))
            if self.path == "/browser/admin" and body.get("action") == "grant":
                status, payload = 200, {"capability": CAPABILITY, "expiresAt": 1}
            elif self.path == "/browser/admin":
                status, payload = 200, {"revoked": True}
            else:
                status, payload = state.reply
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_address[1]}/browser"
    monkeypatch.setenv(SERVICE_URL_ENV, state.url)
    monkeypatch.setenv(ADMIN_TOKEN_ENV, ADMIN)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


def _capability_file(directory: Path, url: str, capability: str = CAPABILITY) -> Path:
    file = directory / "grant.json"
    file.write_text(json.dumps({"url": url, "capability": capability}))
    return file


def test_service_requires_a_local_endpoint_and_admin_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(SERVICE_URL_ENV, raising=False)
    monkeypatch.delenv(ADMIN_TOKEN_ENV, raising=False)
    assert desktop_browser_service() is None
    for url, token in [
        ("https://example.com/browser", ADMIN),
        ("http://127.0.0.1:4100/other", ADMIN),
        ("http://127.0.0.1:4100/browser", "short"),
    ]:
        monkeypatch.setenv(SERVICE_URL_ENV, url)
        monkeypatch.setenv(ADMIN_TOKEN_ENV, token)
        assert desktop_browser_service() is None
    monkeypatch.setenv(SERVICE_URL_ENV, "http://127.0.0.1:4100/browser")
    monkeypatch.setenv(ADMIN_TOKEN_ENV, ADMIN)
    service = desktop_browser_service()
    assert service is not None and service.url == "http://127.0.0.1:4100/browser"


def test_admin_token_never_reaches_child_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ADMIN_TOKEN_ENV, ADMIN)
    assert ADMIN_TOKEN_ENV not in build_subprocess_env()
    assert ADMIN_TOKEN_ENV not in build_subprocess_env({ADMIN_TOKEN_ENV: ADMIN})


def test_target_follows_the_setting_then_desktop_availability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(SERVICE_URL_ENV, raising=False)
    assert browser_target({"remBrowser": {"target": "external"}}, tmp_path) == "external"
    assert browser_target({}, tmp_path) == "external-headless"
    assert browser_target({"remBrowser": {"target": "unknown"}}, tmp_path) == "external-headless"
    monkeypatch.setenv(SERVICE_URL_ENV, "http://127.0.0.1:4100/browser")
    monkeypatch.setenv(ADMIN_TOKEN_ENV, ADMIN)
    assert browser_target(None, tmp_path) == "raticode"
    assert (
        browser_target({"remBrowser": {"target": "external-headless"}}, tmp_path)
        == "external-headless"
    )


def test_each_thread_gets_a_stable_owner() -> None:
    first = browser_owner({"chatThreadId": "thread-1"})
    assert first == browser_owner({"chatThreadId": "thread-1", "id": "other"})
    assert first != browser_owner({"chatThreadId": "thread-2"})
    # Must match remOwnerForThread() in the desktop app.
    assert first == hashlib.sha256(b"thread-1").hexdigest()[:32]
    assert len(first) == 32
    assert browser_owner(None) != browser_owner(None)


def test_raticode_target_grants_a_private_per_turn_capability(
    tmp_path: Path, desktop: _Desktop
) -> None:
    cli = tmp_path / "gof"
    workflow = {
        "chatThreadId": "thread-1",
        "remBrowser": {"target": "raticode"},
        "remResources": {"browser": True},
    }
    project = tmp_path / "project"
    servers = (with_agent_browser(workflow, cli, tmp_path, [project, Path("relative")]) or {})[
        "remResources"
    ]["mcpServers"]
    assert len(servers) == 1
    server = servers[0]
    assert server["command"] == str(cli)
    assert server["args"][:3] == ["ui", "browser", "--capability-file"]
    grant_file = Path(server["args"][3])
    assert grant_file.parent == tmp_path / GRANTS_DIR
    assert read_capability(grant_file) == (desktop.url, CAPABILITY)
    if os.name != "nt":
        assert stat.S_IMODE(grant_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(grant_file.parent.stat().st_mode) == 0o700
    [grant] = desktop.admin("grant")
    assert grant["owner"] == browser_owner(workflow)
    assert grant["uploadRoots"] == [str(project)]
    assert desktop.requests[0][1] == f"Bearer {ADMIN}"
    # No master credential is written anywhere in the data directory.
    assert not (tmp_path / "browser-control.json").exists()
    off = {"remBrowser": {"target": "raticode"}, "remResources": {"browser": False}}
    assert with_agent_browser(off, cli, tmp_path) is off
    assert len(desktop.admin("grant")) == 1


def test_turns_revoke_their_grants(tmp_path: Path, desktop: _Desktop) -> None:
    workflow = {"remBrowser": {"target": "raticode"}, "remResources": {"browser": True}}
    files: list[Path] = []

    @releases_browser_access
    async def run_turn() -> str:
        server = (with_agent_browser(workflow, tmp_path / "gof", tmp_path) or {})["remResources"][
            "mcpServers"
        ][0]
        files.append(Path(server["args"][3]))
        assert files[0].exists()
        return "done"

    assert asyncio.run(run_turn()) == "done"
    assert desktop.admin("revoke") == [{"action": "revoke", "capability": CAPABILITY}]
    assert not files[0].exists()

    @releases_browser_access
    async def stream_turn() -> AsyncGenerator[str, None]:
        with_agent_browser(workflow, tmp_path / "gof", tmp_path)
        yield "first"
        raise RuntimeError("provider failed")

    async def consume() -> list[str]:
        seen: list[str] = []
        with pytest.raises(RuntimeError):
            async for event in stream_turn():
                seen.append(event)
        return seen

    assert asyncio.run(consume()) == ["first"]
    assert len(desktop.admin("revoke")) == 2
    assert list((tmp_path / GRANTS_DIR).iterdir()) == []


def test_raticode_target_explains_when_the_desktop_app_is_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(SERVICE_URL_ENV, raising=False)
    workflow = {"remBrowser": {"target": "raticode"}, "remResources": {"browser": True}}
    with pytest.raises(ValueError, match="Settings > Browser"):
        with_agent_browser(workflow, tmp_path / "gof", tmp_path)


def test_external_targets_use_playwright(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    workflow = {"remBrowser": {"target": "external"}, "remResources": {"browser": True}}
    server = (with_agent_browser(workflow, None, tmp_path) or {})["remResources"]["mcpServers"][0]
    assert server["command"] == "/usr/bin/npx"
    assert "--headless" not in server["args"]


def test_tools_send_the_capability_and_never_an_identity(tmp_path: Path, desktop: _Desktop) -> None:
    desktop.reply = (200, {"tab": "t1", "url": "https://example.com/", "title": "Example"})
    tools = BrowserTools(_capability_file(tmp_path, desktop.url))
    result = tools.call(
        "browser_click", {"ref": "e3", "tab": "t1", "agent": "someone-else", "uploadRoots": ["/"]}
    )
    assert result["title"] == "Example"
    assert desktop.requests == [
        ("/browser", f"Bearer {CAPABILITY}", {"ref": "e3", "tab": "t1", "action": "click"})
    ]


def test_snapshot_and_screenshot_return_mcp_content(tmp_path: Path, desktop: _Desktop) -> None:
    tools = BrowserTools(_capability_file(tmp_path, desktop.url))
    desktop.reply = (200, {"tab": "t1", "snapshot": '- button "Go" [ref=e1]'})
    snapshot = tools.call("browser_snapshot", {})
    assert isinstance(snapshot, ToolContent)
    assert snapshot == [{"type": "text", "text": 'Tab t1\n- button "Go" [ref=e1]'}]
    desktop.reply = (200, {"tab": "t1", "image": "iVBOR", "width": 10, "height": 5})
    image, details = tools.call("browser_take_screenshot", {})
    assert image == {"type": "image", "data": "iVBOR", "mimeType": "image/png"}
    assert json.loads(details["text"]) == {"tab": "t1", "width": 10, "height": 5}


def test_new_tools_map_to_desktop_actions(tmp_path: Path, desktop: _Desktop) -> None:
    tools = BrowserTools(_capability_file(tmp_path, desktop.url))
    tools.call("browser_handle_dialog", {"accept": False})
    tools.call("browser_file_upload", {"ref": "e4", "paths": ["/project/a.pdf"]})
    assert [body["action"] for _path, _token, body in desktop.requests] == ["dialog", "upload"]


def test_desktop_errors_reach_the_agent(tmp_path: Path, desktop: _Desktop) -> None:
    desktop.reply = (400, {"error": "Element e9 is gone. Take a new snapshot."})
    tools = BrowserTools(_capability_file(tmp_path, desktop.url))
    with pytest.raises(ValueError, match="Take a new snapshot"):
        tools.call("browser_click", {"ref": "e9"})
    with pytest.raises(ValueError, match="Unknown browser tool"):
        tools.call("browser_install", {})


def test_ended_access_and_closed_desktop_are_reported(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="access for this turn has ended"):
        BrowserTools(tmp_path / "missing.json").call("browser_tabs", {})
    with pytest.raises(ValueError, match="access for this turn has ended"):
        BrowserTools(_capability_file(tmp_path, "https://example.com/browser")).call(
            "browser_tabs", {}
        )
    with pytest.raises(ValueError, match="Keep the Raticode desktop app open"):
        BrowserTools(_capability_file(tmp_path, "http://127.0.0.1:9/browser")).call(
            "browser_tabs", {}
        )


def test_stdio_passes_content_blocks_through() -> None:
    def call(_name: str, _arguments: dict[str, Any]) -> Any:
        return ToolContent([{"type": "image", "data": "iVBOR", "mimeType": "image/png"}])

    request = {"id": 1, "method": "tools/call", "params": {"name": "shot", "arguments": {}}}
    output = io.StringIO()
    serve_stdio(
        call,
        [],
        "",
        "test",
        input_stream=io.StringIO(json.dumps(request) + "\n"),
        output_stream=output,
    )
    assert json.loads(output.getvalue())["result"]["content"] == [
        {"type": "image", "data": "iVBOR", "mimeType": "image/png"}
    ]


def test_browser_cli_lists_tools_without_internal_fields(tmp_path: Path) -> None:
    request = {"id": 1, "method": "tools/list"}
    result = CliRunner().invoke(
        app,
        ["ui", "browser", "--capability-file", str(tmp_path / "grant.json")],
        input=json.dumps(request) + "\n",
    )
    assert result.exit_code == 0, result.output
    tools = json.loads(result.output)["result"]["tools"]
    assert [tool["name"] for tool in tools] == [tool["name"] for tool in TOOLS]
    assert {"browser_file_upload", "browser_handle_dialog"} <= {tool["name"] for tool in tools}
    assert all("action" not in tool for tool in tools)
    assert all("tab" in tool["inputSchema"]["properties"] for tool in tools)
