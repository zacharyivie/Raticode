"""read-web-page's ``render`` option: browser lookup, the guard proxy and the render itself.

Most tests use a stand-in browser: a small Python script that checks the flags it gets,
fetches the page through the proxy and prints it with a hidden template unwrapped, the way
a script would add content. One test runs a real Chrome or Chromium when one is installed.
"""

from __future__ import annotations

import glob
import json
import os
import socket
import stat
import sys
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from gofer.rattish import web_page, web_render
from gofer.rattish.compiler import CompileContext, RattishCompiler
from gofer.rattish.diagnostics import RattishCompileError
from gofer.rattish.preflight import run_preflight
from gofer.rattish.runtime import execute_node

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the stand-in browser is a POSIX script"
)

RATTISH_ROOT = Path(__file__).parents[2] / "rattish"

ARTICLE = (
    "<html><head><title>Plain</title></head><body><article><h1>Plain article</h1>"
    "<p>" + "This page has its text in the HTML already. " * 10 + "</p></article></body></html>"
)
APP_SHELL = (
    "<html><head><title>App</title><script src=/app.js></script></head><body>"
    '<div id="root"></div><template id=later><article><h1>Built by script</h1>'
    "<p>" + "Text that only appears after the script runs. " * 6 + "</p></article></template>"
    "<noscript>You need to enable JavaScript to run this app.</noscript></body></html>"
)

FAKE_BROWSER = """#!{python}
import json, os, socket, subprocess, sys, time
from urllib.parse import urlsplit

args = sys.argv[1:]
url = args[-1]
flags = dict(a.split("=", 1) if "=" in a else (a, "") for a in args[:-1])
proxy = urlsplit(flags["--proxy-server"])


def get(target):
    sock = socket.create_connection((proxy.hostname, proxy.port), timeout=10)
    sock.sendall(
        f"GET {{target}} HTTP/1.1\\r\\nHost: {{urlsplit(target).netloc}}\\r\\n"
        "Proxy-Connection: keep-alive\\r\\n\\r\\n".encode()
    )
    data = b""
    while chunk := sock.recv(65536):
        data += chunk
    sock.close()
    head, _, body = data.partition(b"\\r\\n\\r\\n")
    return int(head.split()[1]), body.decode()


record = {{
    "args": args,
    "profile_was_empty": os.listdir(flags["--user-data-dir"]) == [],
    "config_home": os.environ.get("XDG_CONFIG_HOME"),
}}
if {mode!r} == "hang":
    helper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    record["helper"] = helper.pid
with open({log!r}, "a") as log:
    log.write(json.dumps(record) + "\\n")
if {mode!r} == "fail":
    sys.stderr.write("[1:1:FATAL:zygote_host.cc:130] No usable sandbox!\\n")
    sys.exit(1)
if {mode!r} == "hang":
    time.sleep(60)
status, body = get(url)
extra = ""
if {secret!r}:
    secret_status, _ = get({secret!r})
    extra = f"<p>The secret request got HTTP {{secret_status}}.</p>"
# Leave something in the profile, as a real browser does.
open(os.path.join(flags["--user-data-dir"], "Cookies"), "w").write("x")
body = body.replace("<template id=later>", "").replace("</template>", "")
print(body.replace("</article>", extra + "</article>"))
"""


def fake_browser(tmp_path: Path, *, mode: str = "render", secret: str = "") -> tuple[str, Path]:
    log = tmp_path / f"browser-{mode}.log"
    script = tmp_path / f"browser-{mode}"
    script.write_text(
        FAKE_BROWSER.format(python=sys.executable, mode=mode, log=str(log), secret=secret)
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script), log


def launches(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def resolver_for(names: dict[str, str]) -> Callable[..., Any]:
    def resolve(host: str, port: int, **_kwargs: Any) -> list[Any]:
        if host in names:
            address = names[host]
        else:
            try:
                socket.inet_aton(host)
            except OSError:
                raise socket.gaierror("not found") from None
            address = host
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    return resolve


class Site:
    """A loopback HTTP/1.1 site that records each request's path and headers."""

    def __init__(self) -> None:
        self.routes: dict[str, tuple[str, str]] = {}
        self.seen: list[tuple[str, dict[str, str]]] = []
        site = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                site.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
                content_type, body = site.routes.get(self.path, ("text/plain", "missing"))
                data = body.encode()
                self.send_response(200 if self.path in site.routes else 404)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def site() -> Iterator[Site]:
    server = Site()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def secret_site() -> Iterator[Site]:
    server = Site()
    server.routes["/secret"] = ("text/plain", "the router password")
    try:
        yield server
    finally:
        server.close()


def proxy_exchange(port: int, request: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        sock.sendall(request)
        data = b""
        while chunk := sock.recv(65536):
            data += chunk
    return data


# Browser lookup ----------------------------------------------------------------------------


def test_find_browser_prefers_chrome_then_chromium_then_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert web_render.find_browser() is None
    for name in ("microsoft-edge", "chromium", "google-chrome"):
        path = tmp_path / name
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
        assert web_render.find_browser() == str(path)


def test_browser_timeout_leaves_time_to_print_the_page() -> None:
    assert web_render.browser_timeout_ms(30) == 25_000
    assert web_render.browser_timeout_ms(8) == 6_000
    assert web_render.browser_timeout_ms(1) == 1_000


# Guard proxy -------------------------------------------------------------------------------


def test_proxy_forwards_checked_http_and_refuses_private_addresses(
    site: Site, secret_site: Site
) -> None:
    site.routes["/page?x=1"] = ("text/html", "<p>hello</p>")
    resolver = resolver_for({"site.test": "127.0.0.1", "localhost": "127.0.0.1"})
    deadline = time.monotonic() + 10
    with web_render.GuardProxy(
        allowlist=["site.test"], resolver=resolver, deadline=deadline
    ) as proxy:
        reply = proxy_exchange(
            proxy.port,
            f"GET http://site.test:{site.port}/page?x=1 HTTP/1.1\r\nHost: site.test:{site.port}"
            "\r\nProxy-Connection: keep-alive\r\nProxy-Authorization: Basic eDp5\r\n\r\n".encode(),
        )
        assert reply.startswith(b"HTTP/1.0 200") or reply.startswith(b"HTTP/1.1 200")
        assert reply.endswith(b"<p>hello</p>")
        path, headers = site.seen[0]
        assert path == "/page?x=1"
        assert headers["connection"] == "close"
        assert "proxy-connection" not in headers and "proxy-authorization" not in headers

        # 127.0.0.1 isn't on the allowlist, and a name that resolves to it isn't either.
        for target in (
            f"http://127.0.0.1:{secret_site.port}/secret",
            f"http://localhost:{secret_site.port}/secret",
        ):
            refused = proxy_exchange(
                proxy.port, f"GET {target} HTTP/1.1\r\nHost: x\r\n\r\n".encode()
            )
            assert refused.startswith(b"HTTP/1.1 403")
        assert secret_site.seen == []
        assert proxy.refused == ["127.0.0.1", "localhost"]


def test_proxy_tunnels_connect_only_to_checked_hosts(site: Site, secret_site: Site) -> None:
    site.routes["/tunnel"] = ("text/plain", "through the tunnel")
    resolver = resolver_for({"site.test": "127.0.0.1", "rebind.test": "127.0.0.1"})
    with web_render.GuardProxy(
        allowlist=["site.test"], resolver=resolver, deadline=time.monotonic() + 10
    ) as proxy:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=10) as sock:
            sock.sendall(f"CONNECT site.test:{site.port} HTTP/1.1\r\n\r\n".encode())
            assert sock.recv(4096).startswith(b"HTTP/1.1 200")
            sock.sendall(b"GET /tunnel HTTP/1.0\r\nHost: site.test\r\n\r\n")
            data = b""
            while chunk := sock.recv(65536):
                data += chunk
        assert data.endswith(b"through the tunnel")

        for target in (
            f"rebind.test:{secret_site.port}",
            f"127.0.0.1:{secret_site.port}",
            "[::1]:443",
        ):
            reply = proxy_exchange(proxy.port, f"CONNECT {target} HTTP/1.1\r\n\r\n".encode())
            assert reply.startswith(b"HTTP/1.1 403"), target
        assert secret_site.seen == []
        assert "rebind.test" in proxy.refused


def test_proxy_stops_accepting_when_closed() -> None:
    proxy = web_render.GuardProxy(
        allowlist=[], resolver=resolver_for({}), deadline=time.monotonic() + 10
    ).start()
    port = proxy.port
    proxy.close()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=2).close()


# Rendering with the stand-in browser -------------------------------------------------------


def read(site: Site, path: str, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("allowlist", ["site.test"])
    kwargs.setdefault("resolver", resolver_for({"site.test": "127.0.0.1"}))
    return web_page.read_web_page(f"http://site.test:{site.port}{path}", **kwargs)


def test_auto_renders_only_pages_that_seem_to_need_javascript(
    tmp_path: Path, site: Site, secret_site: Site
) -> None:
    secret = f"http://127.0.0.1:{secret_site.port}/secret"
    browser, log = fake_browser(tmp_path, secret=secret)
    scratch = tmp_path / "run-scratch" / "run-1"
    site.routes["/plain"] = ("text/html", ARTICLE)
    site.routes["/app"] = ("text/html", APP_SHELL)

    plain = read(site, "/plain", render="auto", scratch_dir=scratch, find_browser=lambda: browser)
    assert plain["rendered"] is False
    assert plain["text"].startswith("# Plain article")
    assert launches(log) == []

    app = read(site, "/app", render="auto", scratch_dir=scratch, find_browser=lambda: browser)
    assert app["rendered"] is True
    assert app["likely_needs_javascript"] is False
    assert app["title"] == "App"
    assert app["text"].startswith("# Built by script")
    # The page's own request to a private address went to the proxy and was refused.
    assert "The secret request got HTTP 403." in app["text"]
    assert "router password" not in app["text"]
    assert secret_site.seen == []
    assert [path for path, _headers in site.seen] == ["/plain", "/app", "/app"]

    (launch,) = launches(log)
    args = launch["args"]
    assert args[-1] == f"http://site.test:{site.port}/app"
    for flag in ("--headless=new", "--dump-dom", "--proxy-bypass-list=<-loopback>"):
        assert flag in args
    assert "--timeout=25000" in args
    assert "--no-sandbox" not in args
    profile = next(arg.split("=", 1)[1] for arg in args if arg.startswith("--user-data-dir="))
    assert Path(profile).parent.parent == scratch
    assert launch["profile_was_empty"] is True
    assert launch["config_home"].startswith(str(scratch))
    # The profile and everything else the render made is gone.
    assert list(scratch.iterdir()) == []


def test_always_renders_and_never_does_not(tmp_path: Path, site: Site) -> None:
    browser, log = fake_browser(tmp_path)
    site.routes["/plain"] = ("text/html", ARTICLE)
    site.routes["/notes.txt"] = ("text/plain", "just text")
    always = read(
        site, "/plain", render="always", scratch_dir=tmp_path, find_browser=lambda: browser
    )
    assert always["rendered"] is True
    assert always["text"].startswith("# Plain article")
    # Plain text has nothing to render.
    text = read(
        site, "/notes.txt", render="always", scratch_dir=tmp_path, find_browser=lambda: browser
    )
    assert text == {**text, "text": "just text", "rendered": False}
    site.routes["/app"] = ("text/html", APP_SHELL)
    never = read(site, "/app", scratch_dir=tmp_path, find_browser=lambda: browser)
    assert never["rendered"] is False
    assert never["likely_needs_javascript"] is True
    assert len(launches(log)) == 1


def test_without_a_browser_auto_keeps_the_plain_page_and_always_fails(
    tmp_path: Path, site: Site
) -> None:
    site.routes["/app"] = ("text/html", APP_SHELL)
    auto = read(site, "/app", render="auto", scratch_dir=tmp_path, find_browser=lambda: None)
    assert auto["rendered"] is False
    assert auto["likely_needs_javascript"] is True
    with pytest.raises(web_page.WebPageError) as caught:
        read(site, "/app", render="always", scratch_dir=tmp_path, find_browser=lambda: None)
    assert caught.value.code == "RATTISH_WEB_PAGE_NO_BROWSER"
    assert "Chrome, Chromium or Edge" in caught.value.message
    with pytest.raises(web_page.WebPageError) as bad:
        read(site, "/app", render="sometimes", find_browser=lambda: None)
    assert bad.value.code == "RATTISH_WEB_PAGE_INVALID_RENDER"


def test_a_browser_that_fails_fails_always_and_auto_falls_back(tmp_path: Path, site: Site) -> None:
    browser, _log = fake_browser(tmp_path, mode="fail")
    site.routes["/app"] = ("text/html", APP_SHELL)
    with pytest.raises(web_page.WebPageError) as caught:
        read(site, "/app", render="always", scratch_dir=tmp_path, find_browser=lambda: browser)
    assert caught.value.code == "RATTISH_WEB_PAGE_BROWSER_FAILED"
    assert "exit 1" in caught.value.message
    assert "No usable sandbox!" in caught.value.message
    auto = read(site, "/app", render="auto", scratch_dir=tmp_path, find_browser=lambda: browser)
    assert auto["rendered"] is False


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    try:
        with open(f"/proc/{pid}/stat") as status:
            return status.read().split(") ", 1)[1].startswith("Z")
    except OSError:
        return False


def test_a_slow_browser_is_stopped_with_its_helpers(tmp_path: Path, site: Site) -> None:
    browser, log = fake_browser(tmp_path, mode="hang")
    site.routes["/app"] = ("text/html", APP_SHELL)
    scratch = tmp_path / "scratch"
    started = time.monotonic()
    with pytest.raises(web_page.WebPageError) as caught:
        read(
            site,
            "/app",
            render="always",
            timeout_seconds=1.5,
            scratch_dir=scratch,
            find_browser=lambda: browser,
        )
    assert caught.value.code == "RATTISH_WEB_PAGE_TIMEOUT"
    assert caught.value.kind == "timeout"
    assert time.monotonic() - started < 10
    (launch,) = launches(log)
    assert "--timeout=1125" in launch["args"]
    deadline = time.monotonic() + 5
    while not _gone(launch["helper"]) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _gone(launch["helper"])
    assert list(scratch.iterdir()) == []


def test_cancel_stops_a_render(tmp_path: Path) -> None:
    browser, _log = fake_browser(tmp_path, mode="hang")
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(web_render.RenderError) as caught:
        web_render.render_page(
            "http://site.test/",
            browser=browser,
            scratch_dir=tmp_path / "scratch",
            timeout_seconds=30,
            cancel=cancel,
        )
    assert caught.value.code == "RATTISH_WEB_PAGE_CANCELLED"
    assert time.monotonic() - started < 10


# Rattish -----------------------------------------------------------------------------------


def compile_source(source: str, project_root: Path) -> dict[str, Any]:
    compiler = RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=[RATTISH_ROOT / "contracts" / "read-web-page.json"],
    )
    return compiler.compile(source, CompileContext("read-web-page", project_root)).ir


def page_source(render: str) -> str:
    return f"""Rattish: 1
Workflow:
  name: Render a page
Node read:
  type: read-web-page
  url: https://example.com/
  render: {render}
"""


def test_render_accepts_never_auto_and_always_in_any_case(tmp_path: Path) -> None:
    node = compile_source(page_source("AUTO"), tmp_path)["nodes"][0]
    assert node["configuration"]["render"] == "auto"
    with pytest.raises(RattishCompileError) as caught:
        compile_source(page_source("sometimes"), tmp_path)
    assert "RATTISH_INVALID_FIELD_VALUE" in {item.code for item in caught.value.diagnostics}


@pytest.mark.parametrize(
    ("render", "severity"), [("always", "error"), ("auto", "warning"), ("never", None)]
)
def test_preflight_says_when_no_browser_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, render: str, severity: str | None
) -> None:
    ir = compile_source(page_source(render), tmp_path)
    monkeypatch.setattr(web_render, "find_browser", lambda: None)
    result = run_preflight(ir, data_dir=tmp_path / "data")
    found = [item for item in result.diagnostics if item.code == "RATTISH_PREFLIGHT_NO_BROWSER"]
    if severity is None:
        assert found == []
    else:
        (diagnostic,) = found
        assert diagnostic.severity == severity
        assert "Chrome, Chromium or Edge" in diagnostic.message
    assert result.ready is (severity != "error")

    monkeypatch.setattr(web_render, "find_browser", lambda: "/usr/bin/google-chrome")
    installed = run_preflight(ir, data_dir=tmp_path / "data")
    assert installed.ready and installed.diagnostics == ()


@pytest.mark.anyio
async def test_node_renders_in_the_runs_scratch_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, site: Site
) -> None:
    browser, log = fake_browser(tmp_path)
    monkeypatch.setattr(web_render, "find_browser", lambda: browser)
    site.routes["/app"] = ("text/html", APP_SHELL)
    source = """Rattish: 1
Workflow:
  name: Render a page
  inputs:
    url:
      schema: {"type": "string"}
      required: true
Node read:
  type: read-web-page
  render: auto
  network-allowlist: ["127.0.0.1"]
  with:
    url: input.url
"""
    data_dir = tmp_path / "data"
    result = await execute_node(
        compile_source(source, tmp_path),
        "read",
        workflow_inputs={"url": f"http://127.0.0.1:{site.port}/app"},
        data_dir=data_dir,
        run_id="run-42",
    )
    assert result.outcome == "success", result.error
    assert result.output["rendered"] is True
    assert result.output["text"].startswith("# Built by script")
    (launch,) = launches(log)
    profile = next(a.split("=", 1)[1] for a in launch["args"] if a.startswith("--user-data-dir="))
    assert Path(profile).is_relative_to(data_dir / "run-scratch" / "run-42")
    assert not Path(profile).exists()


# A real browser ----------------------------------------------------------------------------


def _real_browser() -> str | None:
    found = web_render.find_browser()
    if found:
        return found
    # Playwright's headless shell, when the frontend's browser tests installed it. Tests
    # get their own HOME, so look in the account's real one.
    import pwd

    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    pattern = "chromium_headless_shell-*/*/chrome-headless-shell"
    shells = sorted(glob.glob(str(home / ".cache" / "ms-playwright" / pattern)))
    return shells[-1] if shells else None


REAL_APP = """<!doctype html><html><head><title>Real app</title></head><body>
<div id="root"></div>
<script>
  var out = "not tried";
  try {
    var request = new XMLHttpRequest();
    request.open("GET", "SECRET", false);
    request.send();
    out = "status " + request.status;
  } catch (error) {
    out = "blocked";
  }
  document.getElementById("root").innerHTML =
    "<article><h1>Rendered by Chrome</h1><p>This paragraph was written by a script after " +
    "the page loaded, so only a browser that runs JavaScript can see it.</p>" +
    "<p>Secret request: " + out + "</p></article>";
</script>
<noscript>You need to enable JavaScript.</noscript>
</body></html>"""


@pytest.mark.skipif(sys.platform != "linux", reason="the wrapper is a shell script")
def test_a_real_browser_renders_through_the_guard_proxy(
    tmp_path: Path, site: Site, secret_site: Site
) -> None:
    browser = _real_browser()
    if browser is None:
        pytest.skip("no Chrome, Chromium or Edge is installed")
    # Tests may run where the browser's own sandbox can't start (containers, CI). The
    # product never turns it off; this page is our own.
    wrapper = tmp_path / "browser"
    wrapper.write_text(f'#!/bin/sh\nexec "{browser}" --no-sandbox "$@"\n')
    wrapper.chmod(0o755)
    secret = f"http://127.0.0.1:{secret_site.port}/secret"
    site.routes["/real"] = ("text/html", REAL_APP.replace("SECRET", secret))
    resolver = resolver_for({"site.test": "127.0.0.1"})
    html, truncated, refused = web_render.render_page(
        f"http://site.test:{site.port}/real",
        browser=str(wrapper),
        scratch_dir=tmp_path / "scratch",
        allowlist=["site.test"],
        # A cold browser start on a busy shared runner has needed more than 30 seconds.
        timeout_seconds=90,
        resolver=resolver,
    )
    assert not truncated
    page = web_page.read_html(html, f"http://site.test:{site.port}/real")
    assert page.text.startswith("# Rendered by Chrome")
    assert "router password" not in html
    assert secret_site.seen == []
    assert "127.0.0.1" in refused
    assert list((tmp_path / "scratch").iterdir()) == []
