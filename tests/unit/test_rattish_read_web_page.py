from __future__ import annotations

import gzip
import socket
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from gofer.rattish import web_page
from gofer.rattish.compiler import CompileContext, RattishCompiler
from gofer.rattish.diagnostics import RattishCompileError
from gofer.rattish.preflight import run_preflight
from gofer.rattish.runtime import execute_node
from gofer.ui.link_previews import PreviewError, PrivateAddressError, public_address

PROJECT_ROOT = Path(__file__).parents[2]
RATTISH_ROOT = PROJECT_ROOT / "rattish"

ARTICLE = """<!doctype html>
<html><head>
<title>  Field notes </title>
<meta name="description" content="Notes from the field.">
<base href="https://docs.example/guide/">
</head><body>
<header><a href="/">Site home</a> Site banner</header>
<nav><a href="/menu">Menu link</a></nav>
<main>
  <h1>Main heading outside the article</h1>
  <article>
    <header><h1>The <em>real</em> title</h1></header>
    <p>First   paragraph with a <a href="part-2.html#top">relative link</a>
    and <strong>bold</strong> text.</p>
    <ul><li>One</li><li>Two<ul><li>Two point one</li></ul></li></ul>
    <pre>def f():
    return 1</pre>
    <aside>Sponsored sidebar</aside>
    <div hidden>Hidden text</div>
    <footer>Posted in notes</footer>
  </article>
</main>
<footer>Page footer</footer>
<script>document.write("<p>from a script</p>")</script>
</body></html>
"""


# Rattish ---------------------------------------------------------------------------------


def compiler() -> RattishCompiler:
    return RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=[RATTISH_ROOT / "contracts" / "read-web-page.json"],
    )


def compile_source(source: str, project_root: Path) -> dict[str, Any]:
    return compiler().compile(source, CompileContext("read-web-page", project_root)).ir


def test_contract_defaults_and_url_binding_compile(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Read a page
  inputs:
    url:
      schema: {"type": "string", "minLength": 1}
      required: true
Node read:
  type: read-web-page
  with:
    url: input.url
"""
    node = compile_source(source, tmp_path)["nodes"][0]
    assert node["configuration"] == {
        "max_chars": 20000,
        "format": "markdown",
        "request_timeout": "30s",
        "render": "never",
        "network_allowlist": [],
    }
    assert node["runtime_handler"] == "raticode.read_web_page"


def test_contract_rejects_an_unknown_format(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Bad format
Node read:
  type: read-web-page
  url: https://example.com
  format: pdf
"""
    with pytest.raises(RattishCompileError) as caught:
        compile_source(source, tmp_path)
    assert "RATTISH_INVALID_FIELD_VALUE" in {item.code for item in caught.value.diagnostics}


@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.8", "[::1]"])
def test_preflight_refuses_a_literal_private_address_unless_allowlisted(
    tmp_path: Path, host: str
) -> None:
    source = f"""Rattish: 1
Workflow:
  name: Private page
Node read:
  type: read-web-page
  url: http://{host}/status
"""
    refused = run_preflight(compile_source(source, tmp_path), data_dir=tmp_path / "data")
    assert not refused.ready
    assert [item.code for item in refused.diagnostics] == ["RATTISH_PREFLIGHT_NETWORK_POLICY"]

    allowed_source = source + f"  network-allowlist: [{host.strip('[]')!r}]\n".replace("'", '"')
    allowed = run_preflight(compile_source(allowed_source, tmp_path), data_dir=tmp_path / "data")
    assert allowed.ready, allowed.diagnostics


# Public-address check --------------------------------------------------------------------


def fake_resolver(addresses: dict[str, list[str]]) -> Callable[..., Any]:
    def resolve(host: str, port: int, **_kwargs: Any) -> list[Any]:
        if host not in addresses:
            raise socket.gaierror("not found")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses[host]]

    return resolve


def test_public_address_refuses_private_answers_unless_allowlisted() -> None:
    resolver = fake_resolver(
        {"intranet.example": ["10.1.2.3"], "mixed.example": ["93.184.215.14", "127.0.0.1"]}
    )
    with pytest.raises(PrivateAddressError) as caught:
        public_address("intranet.example", 443, resolver)
    assert caught.value.address == "10.1.2.3"
    # One private answer among public ones is still refused: the socket could use it.
    with pytest.raises(PrivateAddressError):
        public_address("mixed.example", 443, resolver)
    assert (
        public_address("intranet.example", 443, resolver, allowlist=["intranet.example"])
        == "10.1.2.3"
    )
    assert public_address("intranet.example", 443, resolver, allowlist=["*.example"]) == "10.1.2.3"
    assert public_address("intranet.example", 443, resolver, allowlist=["10.0.0.0/8"]) == "10.1.2.3"
    with pytest.raises(PrivateAddressError):
        public_address("intranet.example", 443, resolver, allowlist=["192.168.0.0/16"])
    with pytest.raises(PreviewError):
        public_address("missing.example", 443, resolver)


@pytest.mark.parametrize(
    "address", ["::127.0.0.1", "64:ff9b::7f00:1", "64:ff9b::a01:203", "::ffff:192.168.1.1"]
)
def test_public_address_refuses_ipv6_forms_that_carry_a_private_ipv4(address: str) -> None:
    resolver = fake_resolver({"sneaky.example": [address]})
    with pytest.raises(PrivateAddressError):
        public_address("sneaky.example", 443, resolver)


def test_public_address_accepts_nat64_of_a_public_ipv4() -> None:
    resolver = fake_resolver({"v6only.example": ["64:ff9b::5db8:d70e"]})
    assert public_address("v6only.example", 443, resolver) == "64:ff9b::5db8:d70e"


def test_fetch_refuses_a_host_that_resolves_privately() -> None:
    resolver = fake_resolver({"intranet.example": ["192.168.1.20"]})
    with pytest.raises(web_page.WebPageError) as caught:
        web_page.fetch_page("https://intranet.example/secret?token=abc", resolver=resolver)
    assert caught.value.code == "RATTISH_WEB_PAGE_ADDRESS_REFUSED"
    assert "192.168.1.20" in caught.value.message
    assert "network-allowlist" in caught.value.message
    assert "token" not in str(caught.value.details)


@pytest.mark.parametrize(
    "url",
    ["ftp://example.com/file", "file:///etc/passwd", "https://user:pw@example.com/", "nonsense"],
)
def test_fetch_rejects_urls_it_cannot_read(url: str) -> None:
    with pytest.raises(web_page.WebPageError) as caught:
        web_page.fetch_page(url, resolver=fake_resolver({}))
    assert caught.value.code == "RATTISH_WEB_PAGE_INVALID_URL"
    assert "pw" not in caught.value.message


# Extraction ------------------------------------------------------------------------------


def test_markdown_comes_from_the_article_without_page_chrome() -> None:
    page = web_page.read_html(ARTICLE, "https://docs.example/guide/notes")
    assert page.title == "Field notes"
    assert page.description == "Notes from the field."
    assert page.text.startswith("# The *real* title\n\n")
    assert "[relative link](https://docs.example/guide/part-2.html#top)" in page.text
    assert "**bold**" in page.text
    assert "- One\n- Two\n  - Two point one" in page.text
    assert "```\ndef f():\n    return 1\n```" in page.text
    for left_out in (
        "Site banner",
        "Menu link",
        "Main heading outside",
        "Sponsored",
        "Hidden text",
        "Posted in",
        "Page footer",
        "from a script",
    ):
        assert left_out not in page.text
    # Links are absolute, resolved against <base href>, without fragments.
    assert page.links == [
        "https://docs.example/",
        "https://docs.example/menu",
        "https://docs.example/guide/part-2.html",
    ]
    assert not page.likely_needs_javascript


def test_text_format_has_no_markdown_syntax() -> None:
    page = web_page.read_html(ARTICLE, "https://docs.example/guide/notes", markdown=False)
    assert page.text.startswith("The real title\n\n")
    assert "relative link and bold text." in page.text
    assert "**" not in page.text and "](" not in page.text and "```" not in page.text


def test_main_is_used_when_there_is_no_article_then_the_body() -> None:
    main_page = "<body><nav>Menu</nav><main><p>Main text</p></main><p>Outside</p></body>"
    assert web_page.read_html(main_page, "https://e.example").text == "Main text"
    body_page = "<body><header>Top</header><p>Body text</p><aside>Side</aside></body>"
    assert web_page.read_html(body_page, "https://e.example").text == "Body text"


def test_links_are_capped_at_200_and_skip_non_web_schemes() -> None:
    anchors = "".join(f'<a href="/p/{index}">{index}</a>' for index in range(250))
    page = web_page.read_html(
        f'<body><a href="mailto:a@b.c">m</a><a href="javascript:void(0)">j</a>{anchors}</body>',
        "https://e.example/",
    )
    assert len(page.links) == 200
    assert page.links[0] == "https://e.example/p/0"


def test_script_shell_pages_are_flagged_as_needing_javascript() -> None:
    shell = (
        '<html><head><title>App</title><script src="/app.js"></script></head>'
        '<body><div id="root"></div>'
        "<noscript>You need to enable JavaScript.</noscript></body></html>"
    )
    page = web_page.read_html(shell, "https://app.example/")
    assert page.likely_needs_javascript
    assert page.text == ""


def test_cut_text_marks_truncation_at_a_word_boundary() -> None:
    text, cut = web_page.cut_text("alpha beta gamma delta", 13)
    assert (text, cut) == ("alpha beta", True)
    assert web_page.cut_text("short", 20) == ("short", False)


# Fetching from a local server --------------------------------------------------------------


Route = tuple[int, dict[str, str], bytes]


@pytest.fixture(params=["HTTP/1.0", "HTTP/1.1"])
def site(request: pytest.FixtureRequest) -> Iterator[tuple[str, dict[str, Route], list[str]]]:
    """A loopback site. HTTP/1.0 closes each connection; HTTP/1.1 keeps it open."""
    routes: dict[str, Route] = {}
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = request.param

        def do_GET(self) -> None:  # noqa: N802
            seen.append(self.path)
            status, headers, body = routes.get(self.path, (404, {}, b"missing"))
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", routes, seen
    finally:
        server.shutdown()
        server.server_close()


def test_loopback_is_refused_until_allowlisted(
    site: tuple[str, dict[str, Route], list[str]],
) -> None:
    base, routes, seen = site
    routes["/"] = (200, {"Content-Type": "text/plain"}, b"hello")
    with pytest.raises(web_page.WebPageError) as caught:
        web_page.read_web_page(base + "/")
    assert caught.value.code == "RATTISH_WEB_PAGE_ADDRESS_REFUSED"
    assert seen == []
    page = web_page.read_web_page(base + "/", allowlist=["127.0.0.1"])
    assert page["text"] == "hello"
    assert seen == ["/"]


def test_redirects_are_followed_up_to_five_times(
    site: tuple[str, dict[str, Route], list[str]],
) -> None:
    base, routes, _seen = site
    for index in range(5):
        routes[f"/r{index}"] = (302, {"Location": f"/r{index + 1}"}, b"")
    routes["/r5"] = (200, {"Content-Type": "text/html; charset=utf-8"}, ARTICLE.encode())
    page = web_page.read_web_page(base + "/r0", allowlist=["127.0.0.1"])
    assert page["final_url"] == base + "/r5"
    assert page["title"] == "Field notes"

    routes["/r5"] = (301, {"Location": "/r6"}, b"")
    with pytest.raises(web_page.WebPageError) as caught:
        web_page.read_web_page(base + "/r0", allowlist=["127.0.0.1"])
    assert caught.value.code == "RATTISH_WEB_PAGE_TOO_MANY_REDIRECTS"


def test_a_redirect_to_a_private_address_is_refused(
    site: tuple[str, dict[str, Route], list[str]],
) -> None:
    base, routes, _seen = site
    routes["/go"] = (302, {"Location": "http://10.20.30.40/admin"}, b"")
    with pytest.raises(web_page.WebPageError) as caught:
        web_page.read_web_page(base + "/go", allowlist=["127.0.0.1"])
    assert caught.value.code == "RATTISH_WEB_PAGE_ADDRESS_REFUSED"
    assert "10.20.30.40" in caught.value.message


@pytest.mark.parametrize(
    ("content_type", "reason"),
    [("image/png", "image/png"), ("application/pdf", "application/pdf")],
)
def test_other_content_types_fail_with_the_reason(
    site: tuple[str, dict[str, Route], list[str]], content_type: str, reason: str
) -> None:
    base, routes, _seen = site
    routes["/file"] = (200, {"Content-Type": content_type}, b"\x89PNG....")
    with pytest.raises(web_page.WebPageError) as caught:
        web_page.read_web_page(base + "/file", allowlist=["127.0.0.1"])
    assert caught.value.code == "RATTISH_WEB_PAGE_UNSUPPORTED_TYPE"
    assert reason in caught.value.message
    assert "HTML" in caught.value.message


def test_error_statuses_fail(site: tuple[str, dict[str, Route], list[str]]) -> None:
    base, _routes, _seen = site
    with pytest.raises(web_page.WebPageError) as caught:
        web_page.read_web_page(base + "/nope", allowlist=["127.0.0.1"])
    assert caught.value.code == "RATTISH_WEB_PAGE_HTTP_STATUS"
    assert "404" in caught.value.message


def test_reads_at_most_five_mib_and_marks_truncation(
    site: tuple[str, dict[str, Route], list[str]],
) -> None:
    base, routes, _seen = site
    big = b"word " * (web_page.MAX_BODY_BYTES // 5 + 1000)
    routes["/big"] = (200, {"Content-Type": "text/plain"}, big)
    page = web_page.read_web_page(base + "/big", allowlist=["127.0.0.1"], max_chars=10_000_000)
    assert page["truncated"] is True
    assert len(page["text"]) <= web_page.MAX_BODY_BYTES

    routes["/small"] = (200, {"Content-Type": "text/plain"}, b"one two three four")
    short = web_page.read_web_page(base + "/small", allowlist=["127.0.0.1"], max_chars=8)
    assert short == {
        "title": "",
        "description": "",
        "text": "one two",
        "links": [],
        "final_url": base + "/small",
        "truncated": True,
        "likely_needs_javascript": False,
        "rendered": False,
    }


def test_gzip_pages_and_charsets_are_decoded(
    site: tuple[str, dict[str, Route], list[str]],
) -> None:
    base, routes, _seen = site
    body = "<html><head><title>Café</title></head><body><p>Crème brûlée</p></body></html>"
    routes["/gz"] = (
        200,
        {"Content-Type": "text/html; charset=iso-8859-1", "Content-Encoding": "gzip"},
        gzip.compress(body.encode("iso-8859-1")),
    )
    page = web_page.read_web_page(base + "/gz", allowlist=["127.0.0.1"])
    assert page["title"] == "Café"
    assert page["text"] == "Crème brûlée"


@pytest.mark.anyio
async def test_node_runs_and_reports_policy_failures(
    tmp_path: Path, site: tuple[str, dict[str, Route], list[str]]
) -> None:
    base, routes, _seen = site
    routes["/a"] = (200, {"Content-Type": "text/html"}, ARTICLE.encode())
    source = """Rattish: 1
Workflow:
  name: Read a page
  inputs:
    url:
      schema: {"type": "string"}
      required: true
Node read:
  type: read-web-page
  format: text
  max-chars: 40
  network-allowlist: ["127.0.0.1"]
  with:
    url: input.url
"""
    ir = compile_source(source, tmp_path)
    result = await execute_node(ir, "read", workflow_inputs={"url": base + "/a"})
    assert result.outcome == "success", result.error
    assert result.output["title"] == "Field notes"
    assert result.output["text"].startswith("The real title")
    assert result.output["truncated"] is True
    assert result.output["final_url"] == base + "/a"

    refused_ir = compile_source(
        source.replace('  network-allowlist: ["127.0.0.1"]\n', ""), tmp_path
    )
    refused = await execute_node(refused_ir, "read", workflow_inputs={"url": base + "/a"})
    assert refused.outcome == "failure"
    assert refused.error is not None
    assert refused.error.kind == "network"
    assert refused.error.code == "RATTISH_WEB_PAGE_ADDRESS_REFUSED"


def test_a_template_that_starts_and_ends_with_placeholders_renders_both() -> None:
    from gofer.rattish.prompt_templates import render_template_value

    values = {"link": "https://e.example/", "reason": "refused", "count": 3}
    assert render_template_value("{{link}}: {{reason}}", values) == "https://e.example/: refused"
    # A lone placeholder still keeps the bound value's own type.
    assert render_template_value("{{count}}", values) == 3
