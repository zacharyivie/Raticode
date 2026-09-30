from __future__ import annotations

import io
import json
from email.message import Message
from pathlib import Path
from urllib.request import HTTPHandler, build_opener
from urllib.response import addinfourl

import pytest
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.ui.chat import build_chat_prompt
from gofer.ui.report_outputs import ReportTools, prepare_report, render_pdf, with_report_outputs
from gofer.ui.second_brain import SecondBrain, serve_stdio, with_second_brain

HTML = (
    "<!doctype html><html><head><title>Review</title></head><body>"
    '<section class="page slide"><h1>First finding</h1></section>'
    '<section class="page slide"><h1>Next finding</h1></section></body></html>'
)


def test_slides_save_landscape_document_with_navigation(tmp_path):
    result = ReportTools(tmp_path, "slides").call(
        "save_report",
        {
            "path": "reports/review.html",
            "content": HTML,
        },
    )
    document = Path(result["path"]).read_text()
    assert "aspect-ratio: 16/9" in document
    assert "320mm 180mm" in document
    assert "ArrowRight" in document
    assert "requestFullscreen" in document
    assert "aria-live" in document
    assert "First finding" in document and "Next finding" in document
    with pytest.raises(FileExistsError):
        ReportTools(tmp_path, "slides").call(
            "save_report",
            {
                "path": "reports/review.html",
                "content": HTML,
            },
        )


def test_pdf_saves_real_bytes_and_searchable_source(tmp_path, monkeypatch):
    def render(source):
        assert "break-before: page" in source
        assert "break-inside: avoid" in source
        return b"%PDF-1.7\nfixture"

    monkeypatch.setattr("gofer.ui.report_outputs.render_pdf", render)
    brain = SecondBrain(tmp_path, "pdf", "none")
    result = brain.call("save_note", {"path": "reports/review.pdf", "content": HTML})
    assert Path(result["path"]).read_bytes().startswith(b"%PDF-")
    assert "First finding" in Path(result["sourcePath"]).read_text()
    assert brain.search("finding")[0]["path"] == "reports/review.pdf.html"
    assert result["link"].endswith("review.pdf>)")


def test_pdf_prompt_preserves_theme_in_print():
    prompt = build_chat_prompt(
        "codex",
        "cli-default",
        [],
        {"remReportTheme": {"format": "pdf", "theme": "blueprint", "enabled": True}},
    )
    assert "Preserve the selected report theme in the PDF" in prompt
    assert "without replacing the theme" in prompt
    assert "print-color-adjust: exact" in prompt
    assert "edge-to-edge backgrounds" in prompt
    assert "min-height: 297mm" in prompt
    assert "Blueprint:" in prompt


def test_pdf_render_failure_leaves_no_output(tmp_path, monkeypatch):
    monkeypatch.delenv("RATICODE_REPORT_PDF_URL", raising=False)
    with pytest.raises(ValueError, match="desktop app"):
        ReportTools(tmp_path, "pdf").call(
            "save_report",
            {
                "path": "review.pdf",
                "content": HTML,
            },
        )
    assert not (tmp_path / "review.pdf").exists()
    assert not (tmp_path / "review.pdf.html").exists()


def test_pdf_source_collision_preserves_files(tmp_path, monkeypatch):
    monkeypatch.setattr("gofer.ui.report_outputs.render_pdf", lambda _: b"%PDF-1.7\nfixture")
    (tmp_path / "review.pdf.html").write_text("existing")
    with pytest.raises(FileExistsError):
        ReportTools(tmp_path, "pdf").call(
            "save_report",
            {
                "path": "review.pdf",
                "content": HTML,
            },
        )
    assert not (tmp_path / "review.pdf").exists()
    assert (tmp_path / "review.pdf.html").read_text() == "existing"


@pytest.mark.parametrize("path", ["../escape.html", "/tmp/escape.html", "link/escape.html"])
def test_reports_stay_inside_output_folder(tmp_path, path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(PermissionError):
        ReportTools(root, "html").call("save_report", {"path": path, "content": HTML})


@pytest.mark.parametrize("format", ["slides", "pdf"])
@pytest.mark.parametrize("brain_enabled", [False, True])
def test_format_independent_of_theme_and_second_brain(tmp_path, format, brain_enabled):
    workflow = {
        "remReportTheme": {"enabled": False, "format": format},
        "remSecondBrain": {"enabled": brain_enabled, "root": str(tmp_path), "format": "md"},
    }
    prompt = build_chat_prompt("codex", "cli-default", [], workflow)
    assert f"Default report output: {'Slides' if format == 'slides' else 'PDF'}" in prompt
    assert "design direction" not in prompt
    configured = with_report_outputs(workflow, Path("/trusted/gof"), tmp_path)
    assert configured is not None
    assert configured["remResources"]["mcpServers"][0]["name"] == "reports"
    if brain_enabled:
        configured = with_second_brain(configured, Path("/trusted/gof"))
        assert configured is not None
        assert configured["remResources"]["mcpServers"][-1]["args"][5] == format


def test_explicit_format_overrides_default(tmp_path):
    result = ReportTools(tmp_path, "pdf").call(
        "save_report",
        {
            "path": "report.md",
            "content": "# Findings",
            "format": "md",
        },
    )
    assert Path(result["path"]).read_text() == "# Findings"


def test_stdio_report_error_is_recoverable(tmp_path):
    tools = ReportTools(tmp_path, "slides")
    source = io.StringIO(
        json.dumps(
            {
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "save_report",
                    "arguments": {"path": "report.html", "content": "invalid"},
                },
            }
        )
        + "\n"
    )
    output = io.StringIO()
    serve_stdio(
        tools.call,
        [],
        tools.call("rules", {}),
        "reports",
        input_stream=source,
        output_stream=output,
    )
    assert json.loads(output.getvalue())["result"]["isError"]


def test_reports_cli_saves_slides(tmp_path):
    request = {
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "save_report",
            "arguments": {"path": "report.html", "content": HTML},
        },
    }
    result = CliRunner().invoke(
        app,
        ["ui", "reports", "--root", str(tmp_path), "--report-format", "slides"],
        input=json.dumps(request) + "\n",
    )
    assert result.exit_code == 0, result.output
    assert "isError" not in json.loads(result.output)["result"]
    assert "raticode-slide-controls" in (tmp_path / "report.html").read_text()


def test_pdf_renderer_rejects_nonlocal_endpoint(monkeypatch):
    monkeypatch.setenv("RATICODE_REPORT_PDF_URL", "https://example.com/pdf")
    monkeypatch.setenv("RATICODE_REPORT_PDF_TOKEN", "fixture")
    with pytest.raises(ValueError, match="address"):
        render_pdf(HTML)


@pytest.mark.parametrize("status", [200, 301, 302, 303, 307, 308])
def test_pdf_renderer_never_forwards_credentials_to_redirect(monkeypatch, status):
    requests = []

    class Transport(HTTPHandler):
        # Keep urllib's actual redirect/error processing; replace only network I/O.
        def http_open(self, request):
            requests.append((request.full_url, request.get_header("Authorization"), request.data))
            code = status if request.full_url.endswith("/pdf") else 200
            headers = Message()
            headers["Location"] = "http://collector.test/token"
            response = addinfourl(io.BytesIO(b"%PDF-1.7\nfixture"), headers, request.full_url, code)
            response.msg = "OK" if code == 200 else "Redirect"
            return response

    monkeypatch.setattr(
        "gofer.ui.report_outputs.build_opener",
        lambda *handlers: build_opener(*handlers, Transport()),
    )
    monkeypatch.setenv("RATICODE_REPORT_PDF_URL", "http://127.0.0.1:12345/pdf")
    monkeypatch.setenv("RATICODE_REPORT_PDF_TOKEN", "private-renderer-token")
    if status == 200:
        assert render_pdf(HTML) == b"%PDF-1.7\nfixture"
    else:
        with pytest.raises(ValueError, match="PDF rendering failed"):
            render_pdf(HTML)
    assert requests == [
        ("http://127.0.0.1:12345/pdf", "Bearer private-renderer-token", HTML.encode())
    ]


def test_slides_require_sections():
    with pytest.raises(ValueError, match="section"):
        prepare_report("<html><head></head><body>text</body></html>", "slides")


def test_slides_parse_exact_class_tokens_and_do_not_duplicate_controls():
    with pytest.raises(ValueError, match="section"):
        prepare_report(HTML.replace('class="page slide"', 'class="not-slide"'), "slides")
    prepared = prepare_report(HTML, "slides")
    assert prepare_report(prepared, "slides") == prepared


def test_report_tool_is_not_added_before_global_project_selection(tmp_path):
    workflow = {"remThreads": {"global": True}, "remReportTheme": {"format": "pdf"}}
    assert with_report_outputs(workflow, Path("/trusted/gof"), tmp_path) is workflow


def test_codex_report_tool_has_scoped_grant_and_renderer_environment(tmp_path):
    from gofer.core.prompt_envelope import AgentResources, codex_mcp_server_names
    from gofer.ui.chat import _build_chat_command

    configured = with_report_outputs(
        {"remReportTheme": {"format": "pdf"}}, Path("/trusted/gof"), tmp_path
    )
    assert configured is not None
    resources = AgentResources.model_validate(configured["remResources"])
    name = codex_mcp_server_names(resources, tmp_path)["reports"]
    command = _build_chat_command(
        "codex",
        "cli-default",
        "Create a report.",
        data_dir=tmp_path,
        working_dir=tmp_path,
        resources=resources,
        second_brain_cli_path=Path("/trusted/gof"),
    )
    assert f'mcp_servers.{name}.tools.save_report.approval_mode="approve"' in command
    assert any(
        'env_vars=["RATICODE_REPORT_PDF_URL","RATICODE_REPORT_PDF_TOKEN"]' in arg for arg in command
    )
    resources.mcpServers[0].command = "/untrusted/tool"
    untrusted = _build_chat_command(
        "codex",
        "cli-default",
        "Create a report.",
        data_dir=tmp_path,
        working_dir=tmp_path,
        resources=resources,
        second_brain_cli_path=Path("/trusted/gof"),
    )
    assert not any("save_report.approval_mode" in arg for arg in untrusted)


def test_pdf_reexport_replaces_old_paper_margins():
    old = HTML.replace(
        "</head>",
        "<style data-raticode-pagination>@page { size: A4 portrait; margin: 18mm; }</style></head>",
    )
    prepared = prepare_report(old, "pdf")
    assert prepared.count("data-raticode-pagination") == 1
    assert "margin: 18mm" not in prepared
    assert "margin: 0" in prepared
    assert "min-height: 297mm" in prepared
    assert prepare_report(prepared, "pdf") == prepared
