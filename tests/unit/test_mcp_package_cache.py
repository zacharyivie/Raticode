"""Local MCP servers keep a lasting package cache per server definition."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.core import rem_library
from gofer.rattish import mcp_cache, mcp_tool
from tests.unit.test_rattish_mcp_tool import real_sandbox_problem
from tests.unit.test_rem_library import api as api
from tests.unit.test_rem_library import call


def local(name: str, *args: str) -> dict[str, Any]:
    return {"name": name, "type": "stdio", "command": "npx", "args": ["-y", *args]}


def cache_of(data: Path, server: dict[str, Any]) -> Path:
    return mcp_cache.cache_dir(data, str(mcp_tool.definition_digest(server)))


@pytest.fixture
def no_sandbox_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_tool, "sandbox_problem", lambda *args, **kwargs: None)


def launch(
    data: Path,
    server: dict[str, Any],
    run: str,
    platform: str = "linux",
    *,
    writable_cache: bool = True,
) -> Any:
    return mcp_tool.prepare_launch(
        server,
        mcp_tool.SandboxSettings(network=[]),
        data_dir=data,
        scratch=data / "run-scratch" / run,
        platform=platform,
        writable_cache=writable_cache,
    )


def test_each_definition_gets_its_own_lasting_cache(tmp_path: Path, no_sandbox_check: None) -> None:
    data = tmp_path / "data"
    data.mkdir()
    one, two = local("one", "pkg-one@1.0.0"), local("two", "pkg-two@1.0.0")
    first = launch(data, one, "run-a")
    again = launch(data, one, "run-b")
    other = launch(data, two, "run-a")
    folder = cache_of(data, one)
    assert folder.parent == data.resolve() / "mcp-cache"
    assert folder.name == str(mcp_tool.definition_digest(one)).removeprefix("sha256:")
    # The same definition reuses its folder from run to run; temporary files stay per run.
    for started, run in ((first, "run-a"), (again, "run-b")):
        assert started.env["npm_config_cache"] == str(folder / "npm")
        assert started.env["XDG_CACHE_HOME"] == str(folder / "xdg")
        assert started.env["UV_CACHE_DIR"] == str(folder / "xdg" / "uv")
        assert started.env["TMPDIR"] == str(data / "run-scratch" / run / "tmp")
    # Another server never shares it.
    assert other.env["npm_config_cache"] == str(cache_of(data, two) / "npm")
    assert cache_of(data, two) != folder
    assert (folder / "npm").is_dir() and (folder / "xdg").is_dir()
    if os.name == "posix":
        assert folder.stat().st_mode & 0o777 == 0o700
    # A changed command starts with an empty cache of its own.
    changed = local("one", "pkg-one@1.0.1")
    assert cache_of(data, changed) != folder
    # Unsandboxed, the server keeps the host's own caches.
    off = mcp_tool.prepare_launch(
        one,
        mcp_tool.SandboxSettings(enabled=False),
        data_dir=data,
        scratch=data / "run-scratch" / "off",
        platform="linux",
    )
    assert "npm_config_cache" not in off.env and "UV_CACHE_DIR" not in off.env


def test_sandbox_profiles_make_the_cache_writable(tmp_path: Path, no_sandbox_check: None) -> None:
    data = tmp_path / "data"
    data.mkdir()
    server = local("one", "pkg@1.0.0")
    folder = str(cache_of(data, server))
    argv = " ".join(launch(data, server, "r").argv)
    # The data folder is hidden first, then the server's cache is bound back, writable.
    assert argv.index(f"--tmpfs {data.resolve()}") < argv.index(f"--bind {folder} {folder}")
    profile = launch(data, server, "m", platform="darwin").argv[2]
    lines = profile.splitlines()
    allow_write = next(line for line in lines if line.startswith("(allow file-write*"))
    assert json.dumps(folder) in allow_write
    # Reading and writing it are allowed again after the data folder's deny.
    shown = next(line for line in profile.splitlines() if line.startswith("(allow file-read*"))
    assert shown.startswith("(allow file-read* file-write*") and json.dumps(folder) in shown
    assert profile.index("(deny file-read* file-write*") < profile.index(shown)


def test_tool_calls_read_the_cache_through_a_throwaway_layer(
    tmp_path: Path, no_sandbox_check: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    server = local("one", "pkg@1.0.0")
    folder = str(cache_of(data, server))

    # Linux with overlays: the lasting folder is mounted as an overlay, never bound.
    monkeypatch.setattr(mcp_tool, "_overlay_probe", lambda root: None)
    call = launch(data, server, "c1", writable_cache=False)
    argv = " ".join(call.argv)
    assert f"--overlay-src {folder} --tmp-overlay {folder}" in argv
    assert f"--bind {folder} {folder}" not in argv
    assert argv.index(f"--tmpfs {data.resolve()}") < argv.index("--overlay-src")
    assert call.env["npm_config_cache"] == str(Path(folder) / "npm")
    # A list-only start keeps the writable bind.
    listing = " ".join(launch(data, server, "l1").argv)
    assert f"--bind {folder} {folder}" in listing and "--overlay-src" not in listing

    # Without overlays (bubblewrap older than 0.11), a call before any list-only start
    # filled the cache gets an empty throwaway cache.
    monkeypatch.setattr(mcp_tool, "_overlay_probe", lambda root: "bubblewrap 0.10.0 is older")
    fallback = launch(data, server, "c2", writable_cache=False)
    scratch = data / "run-scratch" / "c2"
    throwaway = Path(fallback.env["npm_config_cache"]).parent
    assert throwaway.parent == scratch and throwaway.name.startswith("mcp-cache-")
    assert fallback.env["XDG_CACHE_HOME"] == str(throwaway / "xdg")
    assert not list((throwaway / "npm").iterdir())
    assert folder not in " ".join(fallback.argv)
    fallback.close()
    assert not throwaway.exists()

    # Once a list-only start has filled it, the call gets a copy in its scratch folder,
    # and the lasting folder is shown read-only, never writable.
    (Path(folder) / "npm" / "pkg.tgz").write_bytes(b"cached")
    mcp_cache.mark_listed(Path(folder))
    copied = launch(data, server, "c3", writable_cache=False)
    copy = Path(copied.env["npm_config_cache"]).parent
    assert copy.parent == data / "run-scratch" / "c3" and copy.name.startswith("mcp-cache-")
    assert (copy / "npm" / "pkg.tgz").read_bytes() == b"cached"
    assert copied.env["UV_CACHE_DIR"] == str(copy / "xdg" / "uv")
    argv = " ".join(copied.argv)
    assert f"--ro-bind {folder} {folder}" in argv
    assert f"--bind {folder} {folder}" not in argv and "--overlay-src" not in argv
    assert argv.index(f"--tmpfs {data.resolve()}") < argv.index(f"--ro-bind {folder} {folder}")
    (copy / "npm" / "pkg.tgz").write_bytes(b"changed in the call")
    assert (Path(folder) / "npm" / "pkg.tgz").read_bytes() == b"cached"
    copied.close()
    assert not copy.exists()
    # A cache over the copy limit, or a copy that fails, leaves the call an empty cache.
    monkeypatch.setattr(mcp_tool, "COPY_LIMIT_BYTES", 3)
    big = launch(data, server, "c4", writable_cache=False)
    assert not list(Path(big.env["npm_config_cache"]).iterdir())
    assert folder not in " ".join(big.argv)
    big.close()
    monkeypatch.setattr(mcp_tool, "COPY_LIMIT_BYTES", 500 * 1024 * 1024)
    real_run = mcp_tool.subprocess.run

    def failing_cp(argv: list[str], **kwargs: Any) -> Any:
        if argv[0] == "cp":
            return real_run(["false"], **kwargs)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(mcp_tool.subprocess, "run", failing_cp)
    failed = launch(data, server, "c5", writable_cache=False)
    assert not list(Path(failed.env["npm_config_cache"]).iterdir())
    # The half-made copy is gone; only the empty cache is left.
    left = list((data / "run-scratch" / "c5").glob("mcp-cache-*"))
    assert left == [Path(failed.env["npm_config_cache"]).parent]
    failed.close()
    monkeypatch.setattr(mcp_tool.subprocess, "run", real_run)

    # macOS: a copy-on-write clone in the run's scratch, writable; the lasting folder is
    # shown read-only, never writable.
    clones: list[tuple[Path, Path]] = []

    def clone(cache: Path, scratch: Path) -> Path:
        target = scratch / "mcp-cache-clone"
        shutil.copytree(cache, target)
        clones.append((cache, target))
        return target

    monkeypatch.setattr(mcp_tool, "_clone_cache", clone)
    mac = launch(data, server, "m1", platform="darwin", writable_cache=False)
    target = data / "run-scratch" / "m1" / "mcp-cache-clone"
    assert clones == [(Path(folder), target)]
    assert mac.env["npm_config_cache"] == str(target / "npm")
    assert mac.env["UV_CACHE_DIR"] == str(target / "xdg" / "uv")
    assert (target / "npm" / "pkg.tgz").read_bytes() == b"cached"
    profile = mac.argv[2].splitlines()
    write_lines = [line for line in profile if "file-write*" in line and "allow" in line]
    assert all(json.dumps(folder) not in line for line in write_lines)
    assert f"(allow file-read* (subpath {json.dumps(folder)}))" in profile
    mac.close()
    assert not target.exists()
    # When cloning fails (not APFS), the call gets an empty throwaway cache.
    monkeypatch.setattr(mcp_tool, "_clone_cache", lambda cache, scratch: None)
    plain = launch(data, server, "m2", platform="darwin", writable_cache=False)
    assert Path(plain.env["npm_config_cache"]).parent.name.startswith("mcp-cache-")
    assert not list(Path(plain.env["npm_config_cache"]).iterdir())
    plain.close()


def test_overlays_need_bubblewrap_0_11(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert mcp_tool.parse_bwrap_version("bubblewrap 0.13.1\n") == (0, 13, 1)
    assert mcp_tool.parse_bwrap_version("bubblewrap 0.10.0") == (0, 10, 0)
    assert mcp_tool.parse_bwrap_version("") is None
    monkeypatch.delitem(mcp_tool._probe_result, "overlay", raising=False)
    monkeypatch.setattr(mcp_tool, "_bwrap_version", lambda: (0, 10, 0))
    monkeypatch.setattr(
        mcp_tool, "_overlay_mount_problem", lambda root: pytest.fail("no mount check")
    )
    reason = mcp_tool._overlay_probe(tmp_path / "mcp-cache")
    assert reason is not None and "0.10.0 is older than 0.11" in reason
    assert mcp_tool.call_cache_mode(tmp_path, "linux") == mcp_tool.CACHE_COPY
    # Asked once per process.
    monkeypatch.setattr(mcp_tool, "_bwrap_version", lambda: pytest.fail("asked again"))
    assert mcp_tool._overlay_probe(tmp_path / "mcp-cache") == reason
    assert mcp_tool.call_cache_mode(tmp_path, "darwin") == mcp_tool.CACHE_CLONE


def test_removing_or_changing_a_server_deletes_its_cache(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    one = local("one", "pkg-one@1.0.0")
    two = local("two", "pkg-two@1.0.0")
    rem_library.save_mcp_server(data, one)
    rem_library.save_mcp_server(data, two)
    for server in (one, two):
        folder = mcp_cache.prepare(data, str(mcp_tool.definition_digest(server)))
        (folder / "npm" / "tarball").write_bytes(b"x" * 10)

    # A description isn't part of the definition, so the cache stays.
    rem_library.save_mcp_server(data, {**one, "description": "Docs"}, replace="one")
    assert cache_of(data, one).is_dir()
    # A changed argument list deletes the old cache; the new definition starts empty.
    changed = local("one", "pkg-one@2.0.0")
    rem_library.save_mcp_server(data, changed, replace="one")
    assert not cache_of(data, one).exists()
    assert not cache_of(data, changed).exists()
    # Removing a server deletes its cache and leaves the others alone.
    assert rem_library.remove_mcp_server(data, "two")
    assert not cache_of(data, two).exists()
    assert (data / "mcp-cache").is_dir()


def test_caches_unused_for_30_days_are_pruned(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    now = time.time()
    fresh = mcp_cache.prepare(data, "sha256:" + "a" * 64)
    stale = mcp_cache.prepare(data, "sha256:" + "b" * 64)
    old = now - 31 * 24 * 60 * 60
    os.utime(stale / mcp_cache.USED_MARKER, (old, old))
    assert mcp_cache.prune(data, now=now) == ["b" * 64]
    assert fresh.is_dir() and not stale.exists()
    # With the list's digests, a folder no server in the list uses goes too.
    assert mcp_cache.prune(data, keep=["sha256:" + "c" * 64], now=now) == ["a" * 64]


def test_starting_a_server_prunes_at_most_daily(tmp_path: Path, no_sandbox_check: None) -> None:
    data = tmp_path / "data"
    data.mkdir()
    server = local("one", "pkg@1.0.0")
    rem_library.save_mcp_server(data, server)
    launch(data, server, "r1")
    # A server left behind by a hand edit of the list, and one unused for a long time.
    orphan = mcp_cache.prepare(data, "sha256:" + "d" * 64)
    assert mcp_cache.prune_if_due(data) == []  # Pruned on the first start already.
    marker = data.resolve() / "mcp-cache" / ".last-prune"
    day_ago = time.time() - mcp_cache.PRUNE_EVERY_SECONDS - 5
    os.utime(marker, (day_ago, day_ago))
    launch(data, server, "r2")
    assert not orphan.exists()
    assert cache_of(data, server).is_dir()


def test_clear_package_cache_from_the_cli_and_the_computer(tmp_path: Path, api: Any) -> None:
    data = tmp_path / "cli-data"
    data.mkdir()
    one, two = local("one", "pkg-one@1.0.0"), local("two", "pkg-two@1.0.0")
    for server in (one, two):
        rem_library.save_mcp_server(data, server)
        folder = mcp_cache.prepare(data, str(mcp_tool.definition_digest(server)))
        (folder / "npm" / "tarball").write_bytes(b"x" * 2048)
    rem_library.save_mcp_server(data, {"name": "docs", "type": "http", "url": "https://x.dev/mcp"})
    runner = CliRunner()
    flags = ["--data-dir", str(data)]

    result = runner.invoke(app, ["rem", "mcp", "clear-cache", "one", *flags])
    assert result.exit_code == 0, result.output
    assert "2.0 KB freed" in result.output
    assert not cache_of(data, one).exists() and cache_of(data, two).is_dir()
    result = runner.invoke(app, ["rem", "mcp", "clear-cache", "docs", *flags])
    assert result.exit_code != 0 and "HTTP server" in result.output
    result = runner.invoke(app, ["rem", "mcp", "clear-cache", "nope", *flags])
    assert result.exit_code != 0 and "No MCP server named nope" in result.output
    result = runner.invoke(app, ["rem", "mcp", "clear-cache", *flags])
    assert result.exit_code != 0 and "--all" in result.output
    result = runner.invoke(app, ["rem", "mcp", "clear-cache", "--all", "--format", "json", *flags])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["bytesFreed"] >= 2048
    assert not cache_of(data, two).exists()

    # The computer's Clear package cache button.
    status, payload = call(api, {"action": "save_mcp", "server": one})
    assert status == 200
    folder = mcp_cache.prepare(api.data_dir, str(mcp_tool.definition_digest(one)))
    (folder / "npm" / "tarball").write_bytes(b"x" * 100)
    status, payload = call(api, {"action": "clear_mcp_cache", "name": "one"})
    assert status == 200
    assert payload["cacheCleared"] >= 100
    assert [server["name"] for server in payload["mcpServers"]] == ["one"]
    assert not folder.exists()
    status, payload = call(api, {"action": "clear_mcp_cache", "name": "missing"})
    assert status == 400 and payload["error"] == "No MCP server named missing"


# A real npx start through the sandbox's proxy, from a stand-in npm registry ---------------

# A tiny MCP server published as an npm package. It answers initialize and lists one tool.
STAND_IN_SERVER = r"""#!/usr/bin/env node
const readline = require("readline");
const send = (message) => process.stdout.write(JSON.stringify(message) + "\n");
readline.createInterface({ input: process.stdin }).on("line", (line) => {
  const message = JSON.parse(line);
  if (message.id === undefined) return;
  if (message.method === "initialize") {
    send({ jsonrpc: "2.0", id: message.id, result: {
      protocolVersion: message.params.protocolVersion,
      capabilities: { tools: {} }, serverInfo: { name: "stand-in", version: "1.0.0" } } });
  } else if (message.method === "tools/list") {
    const hello = { name: "hello", inputSchema: { type: "object" } };
    send({ jsonrpc: "2.0", id: message.id, result: { tools: [hello] } });
  } else if (message.method === "tools/call") {
    const content = [{ type: "text", text: "hi" }];
    send({ jsonrpc: "2.0", id: message.id, result: { content } });
  } else {
    send({ jsonrpc: "2.0", id: message.id, error: { code: -32601, message: "nope" } });
  }
});
"""
PACKAGE = "stand-in-mcp"


def package_tarball() -> bytes:
    files = {
        "package/package.json": json.dumps(
            {"name": PACKAGE, "version": "1.0.0", "bin": {PACKAGE: "server.js"}}
        ).encode(),
        "package/server.js": STAND_IN_SERVER.encode(),
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


@pytest.fixture
def stand_in_registry() -> Iterator[dict[str, Any]]:
    tarball = package_tarball()
    seen: list[str] = []

    class Registry(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            # A forwarding proxy passes on the absolute URL npm asked for.
            path = urlsplit(self.path).path
            seen.append(path)
            host = self.headers.get("Host", "localhost")
            if path.endswith(".tgz"):
                body, kind = tarball, "application/octet-stream"
            elif path.lstrip("/") == PACKAGE:
                integrity = "sha512-" + base64.b64encode(hashlib.sha512(tarball).digest()).decode()
                body = json.dumps(
                    {
                        "name": PACKAGE,
                        "dist-tags": {"latest": "1.0.0"},
                        "versions": {
                            "1.0.0": {
                                "name": PACKAGE,
                                "version": "1.0.0",
                                "bin": {PACKAGE: "server.js"},
                                "dist": {
                                    "tarball": f"http://{host}/{PACKAGE}/-/{PACKAGE}-1.0.0.tgz",
                                    "shasum": hashlib.sha1(tarball).hexdigest(),  # noqa: S324
                                    "integrity": integrity,
                                },
                            }
                        },
                    }
                ).encode()
                kind = "application/json"
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Registry)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield {"url": f"http://localhost:{server.server_address[1]}/", "seen": seen}
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def counting_proxy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every request the sandbox's HostProxy forwards, as ``METHOD target``."""
    requests: list[str] = []
    original = mcp_tool.HostProxy._handle

    class Recording:
        def __init__(self, client: Any) -> None:
            self._client = client
            self._recorded = False

        def recv(self, size: int) -> bytes:
            data: bytes = self._client.recv(size)
            if not self._recorded and data:
                self._recorded = True
                requests.append(" ".join(data.split(b"\r\n", 1)[0].decode("latin-1").split()[:2]))
            return data

        def __getattr__(self, name: str) -> Any:
            return getattr(self._client, name)

    def handle(self: mcp_tool.HostProxy, client: Any) -> None:
        original(self, Recording(client))

    monkeypatch.setattr(mcp_tool.HostProxy, "_handle", handle)
    return requests


NO_OVERLAY = "bubblewrap 0.10.0 is older than 0.11, which added overlay mounts"


def use_sandbox(
    sandbox: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, overlay: str | None = None
) -> None:
    """Start servers in the real bubblewrap sandbox, or skip; or, for ``proxy-only``, set
    up everything a sandboxed start does (cache folder, environment, the HostProxy on a
    loopback port) except bubblewrap itself, for machines where it can't run. There
    ``overlay`` is what the overlay check answers. A ``-copy`` variant forces the overlay
    check to fail, as with bubblewrap older than 0.11, so tool calls get a copy."""
    if sandbox.endswith("-copy"):
        sandbox = sandbox.removesuffix("-copy")
        overlay = NO_OVERLAY
        if sandbox == "bwrap":
            monkeypatch.setattr(mcp_tool, "_overlay_probe", lambda root: NO_OVERLAY)
    if sandbox == "bwrap":
        problem = real_sandbox_problem(tmp_path)
        if problem:
            pytest.skip(f"Needs a working bubblewrap sandbox: {problem}")
    else:
        monkeypatch.setattr(mcp_tool, "sandbox_problem", lambda *args, **kwargs: None)
        monkeypatch.setattr(mcp_tool, "_short_socket_dir", lambda scratch: None)
        monkeypatch.setattr(mcp_tool, "bwrap_command", lambda command, **kwargs: command)
        monkeypatch.setattr(mcp_tool, "_overlay_probe", lambda root: overlay)


def need_overlays(data: Path) -> None:
    problem = mcp_tool._overlay_probe(mcp_cache.cache_root(data))
    if problem:
        pytest.skip(f"Needs bubblewrap overlays: {problem}")


def stand_in_npx(registry: dict[str, Any]) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Needs Node.js.")
    npx = Path(node).resolve().parent / "npx"
    if not npx.exists():
        pytest.skip("npx isn't next to node.")
    return {
        "name": "stand-in",
        "type": "stdio",
        "command": str(npx),
        "args": ["--yes", f"--registry={registry['url']}", f"{PACKAGE}@1.0.0"],
    }


@pytest.mark.parametrize("sandbox", ["bwrap", "proxy-only"])
def test_the_second_start_of_a_server_downloads_no_package(
    tmp_path: Path,
    stand_in_registry: dict[str, Any],
    counting_proxy: list[str],
    sandbox: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = stand_in_npx(stand_in_registry)
    use_sandbox(sandbox, tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    rem_library.save_mcp_server(data, server)
    settings = mcp_tool.SandboxSettings(network=["localhost"], working_dir=work)

    def tarballs() -> list[str]:
        return [request for request in counting_proxy if request.endswith(".tgz")]

    def start() -> list[str]:
        tools = mcp_tool.list_tools(server, settings, data_dir=data, timeout_seconds=90)
        return [tool["name"] for tool in tools]

    assert start() == ["hello"]
    # npm went through the sandbox's proxy, which forwarded the package download.
    assert len(tarballs()) == 1, counting_proxy
    assert sum(path.endswith(".tgz") for path in stand_in_registry["seen"]) == 1
    cache = cache_of(data, server)
    assert (cache / "npm" / "_cacache").is_dir()
    # The second start reuses the server's cache: npm still checks the package's
    # metadata with the registry, but downloads no tarball.
    before = len(counting_proxy)
    assert start() == ["hello"]
    assert len(counting_proxy) > before, "npm still asks the registry for metadata"
    assert len(tarballs()) == 1, counting_proxy
    assert sum(path.endswith(".tgz") for path in stand_in_registry["seen"]) == 1

    # A server with another definition gets its own cache and downloads its own copy.
    other = {**server, "name": "stand-in-2"}
    rem_library.save_mcp_server(data, other)
    assert mcp_tool.list_tools(other, settings, data_dir=data, timeout_seconds=90)
    assert len(tarballs()) == 2
    assert cache_of(data, other) != cache
    # Removing the first server deletes its cache.
    rem_library.remove_mcp_server(data, "stand-in")
    assert not cache.exists() and cache_of(data, other).is_dir()


def lasting_files(cache: Path) -> dict[str, tuple[int, int]]:
    """Each file in a lasting cache with its size and change time, but the used marker."""
    return {
        str(path.relative_to(cache)): (path.lstat().st_size, path.lstat().st_mtime_ns)
        for path in cache.rglob("*")
        if path.name != mcp_cache.USED_MARKER
    }


@pytest.mark.parametrize("sandbox", ["bwrap", "proxy-only", "bwrap-copy", "proxy-only-copy"])
def test_a_pinned_npx_server_called_twice_downloads_once(
    tmp_path: Path,
    stand_in_registry: dict[str, Any],
    counting_proxy: list[str],
    sandbox: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = stand_in_npx(stand_in_registry)
    # proxy-only has no real overlay: the call reads (and could write) the folder itself.
    # The -copy variants force the overlay check to fail, so each call gets a copy.
    use_sandbox(sandbox, tmp_path, monkeypatch, overlay=None)
    copies = sandbox.endswith("-copy")
    data = tmp_path / "data"
    data.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    if sandbox == "bwrap":
        need_overlays(data)
    rem_library.save_mcp_server(data, server)
    settings = mcp_tool.SandboxSettings(network=["localhost"], working_dir=work)
    cache = cache_of(data, server)

    def call(run: str) -> Any:
        return mcp_tool.call_tool(
            server,
            settings,
            "hello",
            {},
            data_dir=data,
            run_id=run,
            timeout_seconds=120,
            structured=False,
        )

    def tarballs() -> int:
        return sum(path.endswith(".tgz") for path in stand_in_registry["seen"])

    # The cache starts empty, so the first call fills it with one list-only start.
    assert not mcp_cache.listed(cache)
    assert call("run-1") == "hi"
    assert mcp_cache.listed(cache)
    assert (cache / "npm" / "_cacache").is_dir()
    assert tarballs() == 1, stand_in_registry["seen"]
    # The next call reads the package from the lasting cache: no download.
    before = lasting_files(cache)
    assert call("run-2") == "hi"
    assert tarballs() == 1, stand_in_registry["seen"]
    if copies:
        # npm wrote to the call's copy only: the lasting cache is as the list-only start
        # left it, and each call's copy went with it.
        assert lasting_files(cache) == before
        assert not list((data / "run-scratch").glob("*/mcp-cache-*"))


# A server whose tool writes into its package cache.
CACHE_WRITER = r"""
import json, os, sys

def cache_paths():
    return [os.path.join(os.environ["npm_config_cache"], "planted"),
            os.path.join(os.environ["XDG_CACHE_HOME"], "planted"),
            os.path.join(os.environ["UV_CACHE_DIR"], "planted")]

def plant_lasting():
    # The lasting folder's own path, when the test gives it: a write there must fail
    # or land in a throwaway layer.
    if os.path.exists("lasting.txt"):
        try:
            with open(open("lasting.txt").read(), "w") as handle:
                handle.write("from a tool call")
        except OSError:
            pass

for line in sys.stdin:
    message = json.loads(line)
    ident, method = message.get("id"), message.get("method")
    if ident is None:
        continue
    params = message.get("params") or {}
    if method == "initialize":
        result = {"protocolVersion": params["protocolVersion"], "capabilities": {"tools": {}},
                  "serverInfo": {"name": "writer", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": name, "inputSchema": {"type": "object"}}
                            for name in ("plant", "look")]}
    else:
        if params["name"] == "plant":
            plant_lasting()
            for path in cache_paths():
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as handle:
                    handle.write("from a tool call")
        cached = os.path.join(os.environ["npm_config_cache"], "package.tgz")
        found = {"planted": [os.path.exists(path) for path in cache_paths()],
                 "cached": os.path.exists(cached)}
        result = {"structuredContent": found, "content": [{"type": "text", "text": "ok"}]}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": ident, "result": result}) + "\n")
    sys.stdout.flush()
"""


@pytest.mark.parametrize("sandbox", ["bwrap", "bwrap-copy", "proxy-only"])
def test_a_tool_call_leaves_nothing_in_the_cache_for_the_next_start(
    tmp_path: Path, sandbox: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # proxy-only stands for a computer without overlays: calls get a throwaway copy.
    use_sandbox(sandbox, tmp_path, monkeypatch, overlay=NO_OVERLAY)
    data = tmp_path / "data"
    data.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    if sandbox == "bwrap":
        need_overlays(data)
    # The script sits in the working folder: the sandbox's /tmp is its own.
    script = work / "writer.py"
    script.write_text(CACHE_WRITER, encoding="utf-8")
    server = {"name": "writer", "type": "stdio", "command": sys.executable, "args": [str(script)]}
    rem_library.save_mcp_server(data, server)
    settings = mcp_tool.SandboxSettings(network=[], working_dir=work)
    cache = cache_of(data, server)
    if sandbox.startswith("bwrap"):
        # In the real sandbox the tool also tries the lasting folder by its own path.
        (work / "lasting.txt").write_text(str(cache / "npm" / "planted"), encoding="utf-8")

    def call(tool: str, run: str) -> Any:
        return mcp_tool.call_tool(
            server,
            settings,
            tool,
            {},
            data_dir=data,
            run_id=run,
            timeout_seconds=30,
            structured=False,
        )

    # A list-only start fills the cache; stand in for a package it downloaded.
    assert [tool["name"] for tool in mcp_tool.list_tools(server, settings, data_dir=data)] == [
        "plant",
        "look",
    ]
    assert mcp_cache.listed(cache)
    (cache / "npm" / "package.tgz").write_bytes(b"package")
    # Inside the call the writes work, and the cached package is there to read.
    assert call("plant", "run-1") == {"planted": [True, True, True], "cached": True}
    # Nothing reached the lasting cache, so the next start sees none of it.
    assert not [path for path in cache.rglob("planted")]
    assert call("look", "run-2") == {"planted": [False, False, False], "cached": True}
    assert not [path for path in cache.rglob("planted")]
    assert (cache / "npm" / "package.tgz").read_bytes() == b"package"
    # The throwaway caches went with their calls.
    assert not list((data / "run-scratch").glob("*/mcp-cache-*"))


# Preflight ---------------------------------------------------------------------------------

CALL_WORKFLOW = """Rattish: 1
Workflow:
  name: Cache
Node call:
  type: mcp-tool
  server: one
  tool: hello
"""


def test_preflight_notes_when_calls_cant_read_the_cache_or_reach_the_package_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.rattish.preflight import run_preflight
    from tests.unit.test_rattish_mcp_tool import compile_source

    monkeypatch.setattr(mcp_tool, "sys", type("Linux", (), {"platform": "linux"}))
    monkeypatch.setattr(mcp_tool, "sandbox_problem", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        mcp_tool,
        "list_tools",
        lambda *args, **kwargs: [{"name": "hello", "inputSchema": {"type": "object"}}],
    )
    probes: list[Path] = []

    def no_overlay(root: Path) -> str:
        probes.append(root)
        return NO_OVERLAY

    monkeypatch.setattr(mcp_tool, "_overlay_probe", no_overlay)
    data = tmp_path / "data"
    data.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    server = local("one", "pkg@1.0.0")
    rem_library.save_mcp_server(data, server)
    cache = mcp_cache.prepare(data, str(mcp_tool.definition_digest(server)))
    (cache / "npm" / "pkg.tgz").write_bytes(b"x" * 64)
    mcp_cache.mark_listed(cache)

    def notes(step: str = "") -> list[Any]:
        ir = compile_source(CALL_WORKFLOW + step, project)
        result = run_preflight(ir, data_dir=data, subscriptions={})
        code = "RATTISH_PREFLIGHT_MCP_CACHE_UNREADABLE"
        found = [d for d in result.diagnostics if d.code == code]
        assert result.ready
        return found

    # A cache small enough to copy: calls read it, so there's nothing to note.
    assert notes() == []
    assert probes == [], "the overlay check waits until the cache is too big to copy"
    # Over the copy limit, without overlays and without the npm registry in network.
    monkeypatch.setattr(mcp_tool, "COPY_LIMIT_BYTES", 10)
    [note] = notes()
    assert note.severity == "info"
    assert note.details["hosts"] == ["registry.npmjs.org"]
    assert note.message == (
        "Tool calls on this computer can't read MCP server 'one''s package cache: "
        f"{NO_OVERLAY}, and the cache holds more than the 0 MB a tool call copies instead. "
        "Each call downloads the package, and the step's network doesn't list "
        "registry.npmjs.org."
    )
    # Listing the package host, or any host, leaves the call able to download.
    assert notes("  network:\n    - registry.npmjs.org\n") == []
    assert notes("  network: any\n") == []
    # With overlays the call reads the lasting cache itself.
    monkeypatch.setattr(mcp_tool, "_overlay_probe", lambda root: None)
    assert notes() == []
    monkeypatch.setattr(mcp_tool, "_overlay_probe", no_overlay)
    # A server that isn't started by a package runner downloads nothing.
    rem_library.save_mcp_server(
        data, {"name": "one", "type": "stdio", "command": "/usr/bin/one-server"}, replace="one"
    )
    assert notes() == []
    # uvx names the Python package hosts.
    python = {"name": "one", "type": "stdio", "command": "uvx", "args": ["pkg==1.0.0"]}
    rem_library.save_mcp_server(data, python, replace="one")
    cache = mcp_cache.prepare(data, str(mcp_tool.definition_digest(python)))
    (cache / "xdg" / "wheel").write_bytes(b"x" * 64)
    mcp_cache.mark_listed(cache)
    [note] = notes("  network:\n    - pypi.org\n")
    assert note.details["hosts"] == ["files.pythonhosted.org"]
