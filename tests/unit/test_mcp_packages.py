"""MCP servers whose package can change: the parser, Review changes, Approve, preflight, CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gofer.cli.client import AGENT_SESSION_ENVS
from gofer.cli.main import app
from gofer.core import rem_library
from gofer.rattish import mcp_tool, trigger_approval
from gofer.rattish.mcp_packages import (
    UNPINNED_WARNING,
    npm_spec_pinned,
    package_uses,
    python_spec_pinned,
    unpinned_packages,
)
from gofer.rattish.preflight import run_preflight
from gofer.rattish.triggers import (
    TriggerStore,
    UnseenChanges,
    approval_status,
    approve_trigger,
    trigger_changes,
)
from tests.unit.test_rattish_mcp_tool import compile_source

runner = CliRunner()


def uses(command: str, *args: str) -> list[tuple[str, str, bool]]:
    return [(use.runner, use.spec, use.pinned) for use in package_uses(command, args)]


@pytest.mark.parametrize(
    ("command", "args", "expected"),
    [
        # npm-style runners
        (
            "npx",
            ["-y", "@modelcontextprotocol/server-github"],
            [("npx", "@modelcontextprotocol/server-github", False)],
        ),
        ("npx", ["-y", "@scope/server@1.2.3"], [("npx", "@scope/server@1.2.3", True)]),
        ("npx", ["server@latest"], [("npx", "server@latest", False)]),
        ("npx", ["server@^1.2.0"], [("npx", "server@^1.2.0", False)]),
        ("npx", ["server@1"], [("npx", "server@1", False)]),
        ("npx", ["server@1.2.3-beta.1"], [("npx", "server@1.2.3-beta.1", True)]),
        (
            "npx",
            ["--yes", "--package=a@1.0.0", "-p", "b", "a-bin"],
            [("npx", "a@1.0.0", True), ("npx", "b", False)],
        ),
        ("npx", ["alias@npm:real@2.0.0"], [("npx", "alias@npm:real@2.0.0", True)]),
        ("npx", ["github:owner/repo"], [("npx", "github:owner/repo", False)]),
        (
            "npx",
            ["github:owner/repo#" + "a" * 40],
            [("npx", "github:owner/repo#" + "a" * 40, True)],
        ),
        ("/usr/local/bin/npx", ["-y", "server"], [("npx", "server", False)]),
        (
            "C:\\Program Files\\nodejs\\npx.cmd",
            ["-y", "server@1.0.0"],
            [("npx", "server@1.0.0", True)],
        ),
        ("pnpx", ["server"], [("pnpx", "server", False)]),
        ("bunx", ["server@1.0.0"], [("bunx", "server@1.0.0", True)]),
        ("bun", ["x", "server"], [("bun x", "server", False)]),
        ("pnpm", ["dlx", "server"], [("pnpm dlx", "server", False)]),
        ("pnpm", ["--silent", "dlx", "server@3.1.4"], [("pnpm dlx", "server@3.1.4", True)]),
        ("yarn", ["dlx", "-p", "server", "bin"], [("yarn dlx", "server", False)]),
        ("npm", ["exec", "--yes", "--", "server"], [("npm exec", "server", False)]),
        # Python runners
        ("uvx", ["mcp-server-fetch"], [("uvx", "mcp-server-fetch", False)]),
        ("uvx", ["mcp-server-fetch@latest"], [("uvx", "mcp-server-fetch@latest", False)]),
        ("uvx", ["mcp-server-fetch@2025.4.7"], [("uvx", "mcp-server-fetch@2025.4.7", True)]),
        ("uvx", ["mcp-server-fetch==2025.4.7"], [("uvx", "mcp-server-fetch==2025.4.7", True)]),
        ("uvx", ["--python", "3.12", "server==1.*"], [("uvx", "server==1.*", False)]),
        ("uvx", ["--from", "server[cli]==1.0", "server-bin"], [("uvx", "server[cli]==1.0", True)]),
        ("uvx", ["--from", "server>=1,<2", "server-bin"], [("uvx", "server>=1,<2", False)]),
        (
            "uvx",
            ["--with", "extra", "server==1.0"],
            [("uvx", "server==1.0", True), ("uvx", "extra", False)],
        ),
        (
            "uvx",
            ["--from", "git+https://github.com/o/r", "bin"],
            [("uvx", "git+https://github.com/o/r", False)],
        ),
        ("uv", ["tool", "run", "server"], [("uv tool run", "server", False)]),
        ("pipx", ["run", "server"], [("pipx run", "server", False)]),
        (
            "pipx",
            ["run", "--spec", "server==1.0.0", "server"],
            [("pipx run", "server==1.0.0", True)],
        ),
        # Wrappers
        ("npx -y server", [], [("npx", "server", False)]),
        ("env", ["TOKEN=x", "npx", "-y", "server"], [("npx", "server", False)]),
        ("cmd", ["/c", "npx", "-y", "server"], [("npx", "server", False)]),
        (
            "bash",
            ["-lc", "cd /srv && exec npx -y server@1.0.0 && uvx other"],
            [("npx", "server@1.0.0", True), ("uvx", "other", False)],
        ),
        # Not a package runner, or nothing fetched
        ("node", ["server.js"], []),
        ("npx", ["./local-server"], []),
        ("uvx", ["/opt/wheels/server-1.0-py3-none-any.whl"], []),
        ("pnpm", ["exec", "server"], []),
        ("pipx", ["install", "server"], []),
        ("github-mcp-server", ["stdio"], []),
    ],
)
def test_package_runners_and_exact_versions(
    command: str, args: list[str], expected: list[tuple[str, str, bool]]
) -> None:
    assert uses(command, *args) == expected


def test_spec_rules() -> None:
    assert npm_spec_pinned("@scope/pkg@1.2.3")
    assert not npm_spec_pinned("@scope/pkg")
    assert not npm_spec_pinned("pkg@1.2.x")
    assert python_spec_pinned("pkg===1.0")
    assert python_spec_pinned("pkg==1.0.0rc1; python_version >= '3.10'")
    assert not python_spec_pinned("pkg~=1.0")
    assert not python_spec_pinned("pkg@1.0")  # only uvx reads @ as a version
    assert python_spec_pinned("pkg@1.0", at_version=True)


def test_only_local_servers_are_checked() -> None:
    assert unpinned_packages({"type": "http", "url": "https://x", "command": "npx"}) == []
    assert unpinned_packages(None) == []
    assert unpinned_packages({"type": "stdio", "command": "npx", "args": ["-y", "a", "b"]}) == ["a"]


def test_definition_text_reads_back() -> None:
    server = {"name": "gh", "type": "stdio", "command": "npx", "args": ["-y", 'we"ird', "x y"]}
    assert mcp_tool.definition_from_text(mcp_tool.definition_text(server)) == server
    remote = {"name": "docs", "type": "http", "url": "https://docs.example.com/mcp"}
    assert mcp_tool.definition_from_text(mcp_tool.definition_text(remote)) == remote
    assert mcp_tool.definition_from_text(None) is None


# Review changes and Approve ---------------------------------------------------------------

WORKFLOW = """Rattish: 1
Workflow:
  name: Issues
Node call:
  type: mcp-tool
  server: gh
  tool: list_issues
"""


@pytest.fixture(autouse=True)
def as_user(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in AGENT_SESSION_ENVS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "data"
    folder.mkdir()
    return folder


@pytest.fixture
def source(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    path = project / "workflow.rattish"
    path.write_text(WORKFLOW, encoding="utf-8")
    return path


def save_server(data_dir: Path, *args: str, command: str = "npx", replace: bool = True) -> None:
    rem_library.save_mcp_server(
        data_dir,
        {"name": "gh", "type": "stdio", "command": command, "args": list(args)},
        replace="gh" if replace else None,
    )


def test_review_changes_flags_an_unpinned_server_and_approve_asks_again(
    data_dir: Path, source: Path
) -> None:
    save_server(data_dir, "-y", "server@1.0.0", replace=False)
    store = TriggerStore(data_dir)
    record, _ = store.create(
        kind="cron", source_path=source, config={"expression": "0 9 * * *", "timezone": "UTC"}
    )
    assert approval_status(store, record).state == "approved"

    # The definition changes to a package with no exact version.
    save_server(data_dir, "-y", "server")
    changes = trigger_changes(store, record.id)
    [entry] = changes["files"]
    assert entry["roleLabel"] == "MCP server"
    assert entry["warning"] == UNPINNED_WARNING
    assert entry["unpinned"] == ["server"]
    assert changes["unseen"] == [
        {
            "path": "mcp-server:gh",
            "absolutePath": "",
            "roleLabel": "MCP server",
            "reason": "unpinned",
            "packages": ["server"],
        }
    ]
    assert f"Warning: {UNPINNED_WARNING} (server)." in trigger_approval.unified_diff_text(changes)

    with pytest.raises(UnseenChanges) as refused:
        approve_trigger(store, record.id, actor="user", by="desktop")
    assert refused.value.files[0]["reason"] == "unpinned"
    message = str(refused.value)
    assert "package version not pinned: each run fetches the newest release" in message
    assert "Pin an exact version in Rem's MCP server list" in message
    assert "weren't shown in full" not in message
    assert approval_status(store, store.get(record.id)).state == "changed"

    approve_trigger(store, record.id, actor="user", by="desktop", accept_unseen=True)
    assert approval_status(store, store.get(record.id)).state == "approved"

    # Pinning it is a change that needs no second yes.
    save_server(data_dir, "-y", "server@2.0.0")
    pinned = trigger_changes(store, record.id)
    assert "warning" not in pinned["files"][0]
    assert pinned["unseen"] == []
    approve_trigger(store, record.id, actor="user", by="desktop")
    assert approval_status(store, store.get(record.id)).state == "approved"


def test_a_removed_unpinned_server_needs_no_second_yes(data_dir: Path, source: Path) -> None:
    save_server(data_dir, "-y", "server", replace=False)
    before = trigger_approval.take_snapshot(source, data_dir)
    source.write_text(
        WORKFLOW.replace("type: mcp-tool", "type: bash-command\n  command: true").replace(
            "  server: gh\n  tool: list_issues\n", ""
        ),
        encoding="utf-8",
    )
    after = trigger_approval.take_snapshot(source, data_dir)
    changes = trigger_approval.describe_changes(before, after)
    removed = next(item for item in changes["files"] if item["change"] == "removed")
    assert removed["role"] == "mcp_server" and "warning" not in removed
    assert changes["unseen"] == []


def test_cli_approve_names_the_unpinned_server_and_needs_unseen(
    data_dir: Path, source: Path
) -> None:
    save_server(data_dir, "-y", "server@1.0.0", replace=False)
    store = TriggerStore(data_dir)
    record, _ = store.create(
        kind="cron", source_path=source, config={"expression": "0 9 * * *", "timezone": "UTC"}
    )
    save_server(data_dir, "-y", "server")
    base = ("--data-dir", str(data_dir))

    shown = runner.invoke(app, ["rattish", "triggers", "changes", record.id, *base])
    assert shown.exit_code == 0, shown.output
    assert UNPINNED_WARNING in shown.output
    assert "Pinning fixes the top-level package only." in shown.output

    refused = runner.invoke(app, ["rattish", "triggers", "approve", record.id, "--yes", *base])
    assert refused.exit_code == 2
    assert "These MCP servers can run a release you haven't seen:" in refused.output
    assert (
        "mcp-server:gh (package version not pinned: each run fetches the newest release: server)"
        in refused.output
    )
    assert "--unseen" in refused.output
    assert approval_status(store, store.get(record.id)).state == "changed"

    as_json = runner.invoke(
        app, ["rattish", "triggers", "approve", record.id, "--yes", "--format", "json", *base]
    )
    assert as_json.exit_code == 2
    assert json.loads(as_json.output)["unseen"][0]["reason"] == "unpinned"

    asked = runner.invoke(app, ["rattish", "triggers", "approve", record.id, *base], input="y\ny\n")
    assert asked.exit_code == 0, asked.output
    assert "Approve them without seeing all of their changes?" in asked.output
    assert approval_status(store, store.get(record.id)).state == "approved"


# Preflight --------------------------------------------------------------------------------


def preflight(data_dir: Path, project: Path) -> dict[str, Any]:
    # sandbox: off keeps preflight from starting the server; this check never starts it.
    ir = compile_source(WORKFLOW.replace("  server: gh", "  sandbox: off\n  server: gh"), project)
    result = run_preflight(ir, data_dir=data_dir, subscriptions={})
    return {item.code: item for item in result.diagnostics}


def test_preflight_warns_about_an_unpinned_package(data_dir: Path, source: Path) -> None:
    save_server(data_dir, "-y", "@modelcontextprotocol/server-github", replace=False)
    found = preflight(data_dir, source.parent)["RATTISH_PREFLIGHT_MCP_PACKAGE_UNPINNED"]
    assert found.severity == "warning"
    assert found.details["packages"] == ["@modelcontextprotocol/server-github"]
    assert found.message == (
        f"MCP server 'gh': {UNPINNED_WARNING} (@modelcontextprotocol/server-github)."
    )
    assert "top-level package only" in " ".join(found.suggestions)

    save_server(data_dir, "-y", "@scope/server@1.2.3")
    assert "RATTISH_PREFLIGHT_MCP_PACKAGE_UNPINNED" not in preflight(data_dir, source.parent)
    save_server(data_dir, "server.py", command=sys.executable)
    assert "RATTISH_PREFLIGHT_MCP_PACKAGE_UNPINNED" not in preflight(data_dir, source.parent)
