"""The CLI parity manifest keeps the CLI in step with the backend, the phone and the window.

docs/cli-parity.json maps every backend HTTP route (one key per body action where a
request field picks what happens), every phone operation and protocol message, and
every request the desktop window sends to Electron to the CLI command that covers it,
or to a named exclusion with a written reason. These tests fail when one is added
without either, when the manifest names a command that doesn't exist, or when a
command isn't catalogued. Fix a failure by adding a CLI command or an exclusion to
the manifest, and describe a new command or exclusion in
website/docs/reference/cli-headless.md.

website/docs/reference/cli-commands.md is generated from the command tree, so every
command, option and help text is on the website. Regenerate it with
``python3 scripts/generate-cli-reference.py`` after changing a command.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import textwrap
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.helpers.cli_parity import (
    ROOT,
    UnreadableRoute,
    _expand_body_actions,
    backend_routes,
    body_actions,
    cli_commands,
    electron_requests,
    load_manifest,
    mapped_commands,
    phone_messages,
    phone_operations,
)

GUIDE = ROOT / "website/docs/reference/cli-headless.md"
COMMAND_KINDS = {"backend", "offline", "legacy", "hidden"}
# Manifest sections that map one surface each to commands or exclusions.
SURFACES = ("routes", "phoneOperations", "phoneMessages", "electron")


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    return load_manifest()


def _check_entries(kind: str, entries: dict[str, Any], manifest: dict[str, Any]) -> None:
    commands = cli_commands()
    for key, entry in entries.items():
        has_cli = "cli" in entry
        has_exclusion = "excluded" in entry
        assert has_cli != has_exclusion, f"{kind} {key!r} needs exactly one of cli or excluded"
        if has_exclusion:
            assert entry["excluded"] in manifest["exclusions"], (
                f"{kind} {key!r} names an unknown exclusion {entry['excluded']!r}"
            )
    missing = sorted(mapped_commands(entries.values()) - set(commands))
    assert not missing, f"The manifest maps {kind}s to commands that don't exist: {missing}"


def test_every_backend_route_has_a_command_or_exclusion(manifest) -> None:
    routes = backend_routes()
    listed = set(manifest["routes"])
    unmapped = sorted(routes - listed)
    assert not unmapped, (
        "These backend routes have no CLI command or written exclusion in "
        f"docs/cli-parity.json: {unmapped}"
    )
    stale = sorted(listed - routes)
    assert not stale, f"docs/cli-parity.json lists routes the backend no longer has: {stale}"
    _check_entries("route", manifest["routes"], manifest)


def test_every_phone_operation_has_a_command_or_exclusion(manifest) -> None:
    operations = phone_operations()
    listed = set(manifest["phoneOperations"])
    assert not sorted(operations - listed), (
        "These phone operations have no CLI command or written exclusion in "
        f"docs/cli-parity.json: {sorted(operations - listed)}"
    )
    assert not sorted(listed - operations), (
        f"docs/cli-parity.json lists phone operations that no longer exist: "
        f"{sorted(listed - operations)}"
    )
    _check_entries("phone operation", manifest["phoneOperations"], manifest)


def _check_surface(kind: str, found: set[str], listed: dict[str, Any]) -> None:
    missing = sorted(found - set(listed))
    assert not missing, (
        f"These {kind}s have no CLI command or written exclusion in docs/cli-parity.json: {missing}"
    )
    stale = sorted(set(listed) - found)
    assert not stale, f"docs/cli-parity.json lists {kind}s that no longer exist: {stale}"


def test_every_phone_message_has_a_command_or_exclusion(manifest) -> None:
    _check_surface("phone message", phone_messages(), manifest["phoneMessages"])
    _check_entries("phone message", manifest["phoneMessages"], manifest)


def test_every_desktop_window_request_has_a_command_or_exclusion(manifest) -> None:
    _check_surface("desktop window request", electron_requests(), manifest["electron"])
    _check_entries("desktop window request", manifest["electron"], manifest)


def test_every_command_is_catalogued(manifest) -> None:
    commands = cli_commands()
    catalogued = manifest["commands"]
    assert set(catalogued.values()) <= COMMAND_KINDS
    assert not sorted(set(commands) - set(catalogued)), (
        "Add these commands to the commands section of docs/cli-parity.json: "
        f"{sorted(set(commands) - set(catalogued))}"
    )
    assert not sorted(set(catalogued) - set(commands)), (
        f"docs/cli-parity.json catalogues commands that don't exist: "
        f"{sorted(set(catalogued) - set(commands))}"
    )
    for name, hidden in commands.items():
        assert (catalogued[name] == "hidden") == hidden, f"{name} hidden={hidden}"


def test_exclusions_are_used_and_documented(manifest) -> None:
    used = {
        entry["excluded"]
        for section in SURFACES
        for entry in manifest[section].values()
        if "excluded" in entry
    }
    assert set(manifest["exclusions"]) == used, "Remove unused exclusions or use them"
    guide = GUIDE.read_text(encoding="utf-8")
    for name, reason in manifest["exclusions"].items():
        assert reason.strip(), f"Exclusion {name} needs a written reason"
        assert f"`{name}`" in guide, f"Describe the {name} exclusion in {GUIDE.name}"


def test_backend_commands_are_documented(manifest) -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    undocumented = [
        name
        for name, kind in manifest["commands"].items()
        if kind == "backend" and not re.search(rf"Raticode {re.escape(name)}\b", guide)
    ]
    assert not undocumented, f"Document these commands in {GUIDE.name}: {undocumented}"


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "generate_cli_reference", ROOT / "scripts/generate-cli-reference.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_website_command_index_matches_the_cli() -> None:
    generator = _generator()
    page = generator.PAGE.read_text(encoding="utf-8")
    assert page == generator.render(), (
        f"{generator.PAGE.relative_to(ROOT)} is out of date. "
        "Run: python3 scripts/generate-cli-reference.py"
    )


def test_website_command_index_names_every_visible_command(manifest) -> None:
    page = _generator().PAGE.read_text(encoding="utf-8")
    headings = set(re.findall(r"^### Raticode (.+)$", page, re.MULTILINE))
    visible = {name for name, kind in manifest["commands"].items() if kind != "hidden"}
    assert headings == visible
    sidebar = (ROOT / "website/.vitepress/sidebar.ts").read_text(encoding="utf-8")
    assert "'reference/cli-commands'" in sidebar


def test_command_index_catches_a_changed_option(monkeypatch) -> None:
    import typer

    generator = _generator()
    before = generator.render()
    from gofer.cli.main import app

    command = typer.main.get_command(app)
    monkeypatch.setattr(typer.main, "get_command", lambda _app: command)
    doctor = command.commands["doctor"]  # type: ignore[attr-defined]
    option = next(p for p in doctor.params if "--workflow" in p.opts)
    monkeypatch.setattr(option, "help", "A changed help text.")
    assert "A changed help text." in generator.render()
    assert generator.render() != before


def test_command_index_text_cannot_break_the_website() -> None:
    escape = _generator()._escape
    assert escape("{{secret.X}} <data> [Legacy] a|b") == (
        "&#123;&#123;secret.X&#125;&#125; &#60;data&#62; &#91;Legacy&#93; a&#124;b"
    )
    assert escape("Use `Raticode rattish` here") == "Use `Raticode rattish` here"
    assert escape("Use `{{x}}` here").count("&#123;") == 2


# The reader itself ----------------------------------------------------------------

_HANDLER = """
class GoferUiRequestHandler:
    def _dispatch_request(self, method):
        try:
            parsed = urlparse(self.path)
        except ValueError:
            return
        try:
            if parsed.path.startswith("/api/rem/"):
                self._dispatch_rem_home_request(method)
                return
            if parsed.path == "/api/swarms" or parsed.path.startswith("/api/swarms/"):
                self._dispatch_swarm_request(method)
                return
        finally:
            pass

    def _dispatch_rem_home_request(self, method):
        parts = []
        if parts[0] == "kanban":
            pass
        if method == "GET" and parts == ["tasks"]:
            pass

    def _rem_kanban_request(self, method, parts, known):
        if method == "GET" and not parts:
            pass

    def _dispatch_rem_resources_request(self, method):
        pass

    def _dispatch_swarm_request(self, method):
        if method == "GET" and not swarm_id:
            pass

    def _dispatch_rattish_triggers(self, method, path, query):
        if method == "GET" and not action:
            pass

    def _dispatch_GET(self):
        if parsed.path == "/api/health":
            pass
        if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/logs"):
            pass
        {extra}

    def _dispatch_POST(self):
        if parsed.path in {{"/api/chat/steer", "/api/chat/stop"}}:
            if parsed.path == "/api/chat/steer":
                pass

    def _dispatch_PUT(self):
        pass

    def _dispatch_DELETE(self):
        pass
"""


def _routes(extra: str = "pass") -> set[str]:
    source = textwrap.dedent(_HANDLER).replace("{extra}", extra).replace("{{", "{")
    return backend_routes(source.replace("}}", "}"))


def test_reader_finds_a_newly_added_route() -> None:
    before = _routes()
    assert {
        "GET /api/health",
        "GET /api/workflows/*/logs",
        "POST /api/chat/steer",
        "POST /api/chat/stop",
        "GET /api/rem/tasks",
        "GET /api/rem/kanban",
        "GET /api/swarms",
        "GET /api/rattish/triggers",
    } <= before
    after = _routes('if parsed.path == "/api/brand-new":\n            pass')
    assert after - before == {"GET /api/brand-new"}


def test_reader_finds_a_route_nested_in_another_routes_body() -> None:
    before = _routes()
    nested = (
        'if parsed.path.startswith("/api/workflows/") and parsed.path.endswith("/logs"):\n'
        '            if parsed.path.endswith("/brand-new/logs"):\n'
        "                pass"
    )
    assert _routes(nested) - before == {"GET /api/workflows/*/brand-new/logs"}
    # A nested check that only repeats its route adds nothing.
    same = (
        'if parsed.path == "/api/health":\n'
        '            if parsed.path == "/api/health":\n'
        "                pass"
    )
    assert _routes(same) == before


def test_reader_refuses_a_nested_check_that_cannot_match() -> None:
    nested = (
        'if parsed.path == "/api/health":\n'
        '            if parsed.path == "/api/other":\n'
        "                pass"
    )
    with pytest.raises(UnreadableRoute, match="never match"):
        _routes(nested)


def test_reader_gives_each_body_action_its_own_route() -> None:
    from tests.helpers.cli_parity import KANBAN

    source = KANBAN.read_text()
    actions = body_actions("POST /api/rem/kanban/ticket")
    assert {"create", "move", "approve", "decline", "comment"} <= actions
    archive = 'elif action == "archive":\n            pass\n        elif action == "comment":'
    added = source.replace('elif action == "comment":', archive)
    assert added != source
    assert body_actions("POST /api/rem/kanban/ticket", added) - actions == {"archive"}
    routes = _expand_body_actions({"POST /api/rem/kanban/ticket", "GET /api/health"})
    assert "POST /api/rem/kanban/ticket" not in routes
    assert "POST /api/rem/kanban/ticket#move" in routes
    assert "GET /api/health" in routes


def test_device_settings_actions_are_each_checked() -> None:
    routes = backend_routes()
    for action in ("wake_computer", "workspace_exchange", "host_template", "authorize_thread"):
        assert f"POST /api/devices#{action}" in routes
    assert "POST /api/devices" not in routes
    assert "POST /api/swarms/*/control#pause" in routes


def test_reader_finds_new_phone_messages_and_actions() -> None:
    from tests.helpers.cli_parity import PROTOCOL

    schema = json.loads(PROTOCOL.read_text())
    before = phone_messages(schema)
    assert {"thread.create", "job.submit", "swarm.control#pause", "approval.resolve#deny"} <= before
    assert not {key for key in before if key.startswith("desktop.")}
    changed = copy.deepcopy(schema)
    changed["properties"]["type"]["enum"].append("thread.export")
    for rule in changed["allOf"]:
        if rule["if"]["properties"]["type"]["const"] == "thread.manage":
            rule["then"]["properties"]["payload"]["properties"]["action"]["enum"].append("rename")
        if rule["if"]["properties"]["type"]["const"] == "desktop.action":
            rule["then"]["properties"]["payload"]["properties"]["op"]["enum"].append("rem.export")
    assert phone_messages(changed) - before == {"thread.export", "thread.manage#rename"}
    assert phone_operations(changed) - phone_operations(schema) == {"rem.export"}


def test_reader_finds_new_desktop_window_requests(tmp_path: Path) -> None:
    from tests.helpers.cli_parity import ELECTRON

    root = tmp_path / "electron"
    root.mkdir()
    (root / "ipc-handlers.cjs").write_text(
        (ELECTRON / "ipc-handlers.cjs")
        .read_text()
        .replace(
            '["gofer:clipboard-read", "readClipboard"],',
            '["gofer:clipboard-read", "readClipboard"],\n'
            '  ["gofer:export-thread", "exportThread"],',
        )
    )
    (root / "main.js").write_text('ipcMain.on("gofer:migrate-legacy-projects", migrate);\n')
    found = electron_requests(root)
    assert found - electron_requests() == {"gofer:export-thread"}
    (root / "extra.cjs").write_text("ipcMain.handle('gofer:secret-thing', handler);\n")
    assert "gofer:secret-thing" in electron_requests(root)
    (root / "sneaky.js").write_text("ipcMain.handle(channelName, handler);\n")
    with pytest.raises(UnreadableRoute, match="sneaky.js"):
        electron_requests(root)


def test_relabelled_entries_say_what_they_are(manifest) -> None:
    routes = manifest["routes"]
    for action in ("approve", "review", "reopen"):
        assert routes[f"POST /api/organizations#{action}"] == {"excluded": "user-decision"}
    assert routes["POST /api/organizations#memory"] == {"excluded": "worker-tools"}
    assert routes["POST /api/organizations#restore"] == {"excluded": "consent-gated"}
    assert routes["POST /api/workflows/*/rename"] == {"cli": "workflow rename"}
    assert routes["POST /api/workflows/*/duplicate"] == {"cli": "workflow duplicate"}
    assert routes["DELETE /api/workflows/*"] == {"cli": "workflow rm"}


def test_reader_refuses_a_path_condition_it_cannot_read() -> None:
    with pytest.raises(UnreadableRoute):
        _routes('if re.match(r"/api/x/\\d+", parsed.path):\n            pass')


def test_org_hold_guard_matches_the_backend() -> None:
    from gofer.cli.commands.org import _UNSANDBOXED
    from gofer.ui.organization_store import DANGEROUS_PERMISSION_MODES

    assert _UNSANDBOXED == set(DANGEROUS_PERMISSION_MODES)
