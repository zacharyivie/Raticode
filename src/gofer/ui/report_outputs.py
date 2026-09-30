"""Standalone report formats and file output, independent of knowledge storage."""

from __future__ import annotations

import os
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from gofer.core.http import read_response_bytes
from gofer.core.prompt_envelope import AgentResources, McpReference
from gofer.utils.atomic_output import atomic_binary_output

REPORT_FORMATS = {"md", "html", "slides", "pdf"}
MAX_REPORT_BYTES = 2 * 1024 * 1024


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        # The renderer token belongs only to the configured desktop endpoint.
        return None


class _SlideSections(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "section" and "slide" in (dict(attrs).get("class") or "").split():
            self.count += 1


def report_format_rules(report_format: str) -> str:
    common = "Use the user's explicitly requested format when it differs from this default. "
    if report_format == "slides":
        return (
            "Default report output: Slides. Generate a single-file HTML slideshow in landscape "
            "16:9 PowerPoint-style format. Write a complete HTML document with embedded CSS and "
            'assets. Put each slide in a <section class="slide">, with one main idea per slide, '
            "large readable type, and concise content that fits without clipping. The save tool "
            "adds Previous/Next, arrow-key navigation, slide counts, fullscreen, and one slide "
            "per landscape printed page. Do not add your own navigation or hide slides in CSS. "
            "Save with a .html extension. " + common
        )
    if report_format == "pdf":
        return (
            "Default report output: PDF. Author a complete standalone HTML document with "
            "embedded CSS and assets as the source for PDF rendering. Use A4 portrait pages "
            "with edge-to-edge backgrounds and artwork, without a white paper border. Use "
            "@page { size: A4 portrait; margin: 0; } and zero html/body margin and padding. "
            "Keep comfortable text insets inside each page with border-box padding, not "
            "paper margins. Give each page min-height: 297mm so short pages fill the sheet; "
            "allow longer content to flow onto additional pages. Put separated pages in <section "
            'class="page"> elements. Keep headings with the following text and avoid splitting '
            "figures and table rows; split long content into more pages rather than clipping it. "
            "Do not use fixed page heights or overflow:hidden. Preserve the selected report "
            "theme in the PDF, including its backgrounds, colors, typography, and artwork. "
            "Print CSS should adapt spacing and pagination without replacing the theme with "
            "a white background and black text or hiding illustrations. Use print-color-adjust: "
            "exact and -webkit-print-color-adjust: exact. Use @page and print CSS, embed "
            "all charts as SVG or images, and avoid JavaScript and remote assets. Call save_report "
            "or Second Brain save_note with a .pdf path and the HTML source as content. Raticode "
            "renders a real PDF, retains a searchable .pdf.html source, and returns the PDF link. "
            "Do not rename HTML to .pdf or claim a PDF exists before the save succeeds. " + common
        )
    return (
        f"Default report output: {'Markdown' if report_format == 'md' else 'standalone HTML'}. "
        + common
    )


PRINT_STYLES = """<style data-raticode-pagination>
@page { size: A4 portrait; margin: 0; }
@media print {
  html, body { margin: 0; padding: 0;
    print-color-adjust: exact; -webkit-print-color-adjust: exact; }
  :where(.page, [data-page]) { box-sizing: border-box; min-height: 297mm; padding: 18mm; }
  .page ~ .page, [data-page] ~ [data-page] { break-before: page; }
  h1, h2, h3, h4 { break-after: avoid; }
  figure, img, tr { break-inside: avoid; }
  p, li { orphans: 3; widows: 3; }
  thead { display: table-header-group; }
  img, svg { max-width: 100%; }
}
</style>"""

SLIDE_STYLES = """<style data-raticode-slides>
html { scroll-behavior: auto; }
body { margin: 0; }
.slide { box-sizing: border-box; width: 100%; aspect-ratio: 16/9; padding: 4%; }
@media screen {
  html { background: #202124; }
  body { width: min(100vw, calc((100dvh - 64px) * 16 / 9)); margin: 0 auto; }
  .slide { overflow: auto; }
  .slide[hidden] { display: none !important; }
  #raticode-slide-controls { position: fixed; bottom: 0; left: 0; right: 0; height: 56px;
    display: flex; align-items: center; justify-content: center; gap: 16px;
    background: #202124; color: #fff; font: 14px system-ui, sans-serif; }
  #raticode-slide-controls button { font: inherit; padding: 6px 12px; color: #fff;
    background: #35363a; border: 1px solid #9aa0a6; border-radius: 4px; cursor: pointer; }
  #raticode-slide-controls button:disabled { opacity: .45; cursor: default; }
  #raticode-slide-controls button:focus-visible { outline: 2px solid #8ab4f8; }
}
@page { size: 320mm 180mm; margin: 0; }
@media print {
  body { width: 320mm; margin: 0; }
  .slide, .slide[hidden] { display: block !important; width: 320mm; min-height: 180mm;
    break-after: page; print-color-adjust: exact; -webkit-print-color-adjust: exact; }
  .slide:last-of-type { break-after: auto; }
  #raticode-slide-controls { display: none; }
}
</style>"""

SLIDE_CONTROLS = """<nav id="raticode-slide-controls" aria-label="Presentation controls">
<button type="button" data-prev>Previous</button><span aria-live="polite" data-count></span>
<button type="button" data-next>Next</button><button type="button" data-full>Fullscreen</button>
</nav><script>
(() => {
  const slides = [...document.querySelectorAll('section.slide')];
  const controls = document.getElementById('raticode-slide-controls');
  let index = Math.max(0, Math.min(slides.length - 1, Number(location.hash.slice(1)) - 1 || 0));
  function show(next) {
    index = Math.max(0, Math.min(slides.length - 1, next));
    slides.forEach((slide, n) => { slide.hidden = n !== index;
      slide.setAttribute('aria-label', `Slide ${n + 1} of ${slides.length}`); });
    controls.querySelector('[data-count]').textContent = `${index + 1} / ${slides.length}`;
    controls.querySelector('[data-prev]').disabled = index === 0;
    controls.querySelector('[data-next]').disabled = index === slides.length - 1;
    history.replaceState(null, '', '#' + (index + 1));
  }
  controls.querySelector('[data-prev]').onclick = () => show(index - 1);
  controls.querySelector('[data-next]').onclick = () => show(index + 1);
  controls.querySelector('[data-full]').onclick = async () => {
    try { if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen(); } catch { /* Optional. */ }
  };
  document.addEventListener('keydown', event => {
    if (event.target.closest?.('input, textarea, select, button, a, [contenteditable]')) return;
    if (['ArrowRight', 'PageDown', ' ', 'ArrowLeft', 'PageUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      show(event.key === 'Home' ? 0 : event.key === 'End' ? slides.length - 1 :
        index + (['ArrowLeft', 'PageUp'].includes(event.key) ? -1 : 1));
    }
  });
  show(index);
})();
</script>"""


def prepare_report(content: str, report_format: str) -> str:
    if report_format in {"pdf", "slides"}:
        if (
            not re.search(r"<html[\s>]", content, re.I)
            or not re.search(r"</head\s*>", content, re.I)
            or not re.search(r"</body\s*>", content, re.I)
        ):
            raise ValueError("Provide a complete HTML document with head and body elements.")
        if report_format == "slides":
            sections = _SlideSections()
            sections.feed(content)
            if not sections.count:
                raise ValueError('Slides need a <section class="slide"> for each slide.')
        # Replace earlier converter defaults as well as the current version on re-export.
        content = re.sub(
            r"<style\b[^>]*\bdata-raticode-pagination(?:\s|>)[\s\S]*?</style\s*>",
            "",
            content,
            flags=re.I,
        )
        content = content.replace(SLIDE_STYLES, "")
        content = content.replace(SLIDE_CONTROLS, "")
        content = re.sub(
            r"</head\s*>",
            lambda _: (SLIDE_STYLES if report_format == "slides" else PRINT_STYLES) + "</head>",
            content,
            count=1,
            flags=re.I,
        )
        if report_format == "slides":
            content = re.sub(
                r"</body\s*>", lambda _: SLIDE_CONTROLS + "</body>", content, count=1, flags=re.I
            )
    return content


def render_pdf(content: str) -> bytes:
    url, token = (
        os.environ.get("RATICODE_REPORT_PDF_URL"),
        os.environ.get("RATICODE_REPORT_PDF_TOKEN"),
    )
    if not url or not token:
        raise ValueError("PDF rendering requires the Raticode desktop app. Open it and retry.")
    if not re.fullmatch(r"http://127\.0\.0\.1:\d+/pdf", url):
        raise ValueError("Invalid desktop PDF renderer address.")
    request = Request(
        url,
        content.encode("utf-8"),
        {"Authorization": f"Bearer {token}", "Content-Type": "text/html; charset=utf-8"},
    )
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=55) as response:
            data = read_response_bytes(response, 32 * 1024 * 1024)
    except (URLError, TimeoutError) as exc:
        raise ValueError("PDF rendering failed. Keep the desktop app open and retry.") from exc
    if not data.startswith(b"%PDF-") or len(data) > 32 * 1024 * 1024:
        raise ValueError("The renderer did not return a valid PDF under 32 MB.")
    return data


def save_report(root: Path, relative: str, content: Any, report_format: str) -> dict[str, str]:
    if report_format not in REPORT_FORMATS:
        raise ValueError("Choose Markdown, HTML, Slides, or PDF.")
    target = (root / relative).resolve()
    if not relative or Path(relative).is_absolute() or not target.is_relative_to(root):
        raise PermissionError("Use a report path inside the output folder.")
    extension = "html" if report_format == "slides" else report_format
    if target.suffix.lower() != f".{extension}":
        raise ValueError(f"Save reports with the configured .{extension} extension.")
    if not isinstance(content, str) or len(content.encode()) > MAX_REPORT_BYTES:
        raise ValueError("Provide report text of at most 2 MB.")
    if target.exists():
        raise FileExistsError(f"Report already exists: {target.name}")
    prepared = prepare_report(content, report_format)
    if len(prepared.encode("utf-8")) > MAX_REPORT_BYTES:
        raise ValueError("The prepared report exceeds 2 MB. Shorten the report or its assets.")
    data = render_pdf(prepared) if report_format == "pdf" else prepared.encode("utf-8")
    result = {"path": str(target), "link": f"[{target.stem}](<{target}>)"}
    # Keep the authored PDF source searchable by existing knowledge tools.
    with atomic_binary_output(root / relative, exclusive=True) as output:
        output.write(data)
        if report_format == "pdf":
            source = root / (relative + ".html")
            with atomic_binary_output(source, exclusive=True) as source_output:
                source_output.write(prepared.encode("utf-8"))
            result["sourcePath"] = str(source)
    return result


def with_report_outputs(
    workflow: dict[str, Any] | None, cli_path: Path | None, root: Path
) -> dict[str, Any] | None:
    config = (workflow or {}).get("remReportTheme") or {}
    if not config.get("format") or ((workflow or {}).get("remThreads") or {}).get("global"):
        return workflow
    if cli_path is None:
        raise ValueError("The Raticode CLI is unavailable for report output tools.")
    if config["format"] not in REPORT_FORMATS:
        raise ValueError("Choose Markdown, HTML, Slides, or PDF for reports.")
    resources = AgentResources.model_validate((workflow or {}).get("remResources") or {})
    resources.mcpServers = [item for item in resources.mcpServers if item.name != "reports"]
    resources.mcpServers.append(
        McpReference(
            name="reports",
            type="stdio",
            command=str(cli_path),
            args=["ui", "reports", "--root", str(root), "--report-format", config["format"]],
        )
    )
    return {**(workflow or {}), "remResources": resources.model_dump()}


class ReportTools:
    def __init__(self, root: Path, report_format: str) -> None:
        self.root = root.resolve(strict=True)
        if not self.root.is_dir() or report_format not in REPORT_FORMATS:
            raise ValueError("Reports need an output folder and a supported format.")
        self.report_format = report_format

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "rules":
            return report_format_rules(self.report_format) + (
                f" Save reports relative to {self.root} using save_report. "
                "Existing files are kept. Include the returned local link in your response."
            )
        if name == "save_report":
            return save_report(
                self.root,
                str(arguments.get("path", "")),
                arguments.get("content"),
                arguments.get("format", self.report_format),
            )
        raise ValueError("Unknown report tool.")


def serve_reports(root: Path, report_format: str) -> None:
    from gofer.ui.second_brain import serve_stdio

    reports = ReportTools(root, report_format)
    definitions = [
        {
            "name": "save_report",
            "description": reports.call("rules", {})
            + " Supply Markdown or HTML content. PDF accepts HTML source; "
            "Slides accepts section.slide.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "format": {"type": "string", "enum": sorted(REPORT_FORMATS)},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": False,
                "openWorldHint": False,
            },
        }
    ]
    serve_stdio(reports.call, definitions, reports.call("rules", {}), "raticode-reports")
