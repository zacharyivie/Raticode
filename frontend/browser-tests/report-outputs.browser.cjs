/* global __dirname, console, process, setTimeout, clearTimeout, fetch */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { inflateSync } = require("node:zlib");
const { Buffer } = require("node:buffer");
const { execFileSync, spawn } = require("node:child_process");
const { app, BrowserWindow, session } = require("electron");
const { startReportPdfService } = require("../electron/report-pdf.cjs");
const root = path.resolve(__dirname, "../..");
const output = fs.mkdtempSync(path.join(os.tmpdir(), "raticode-report-outputs-"));
app.setPath("userData", path.join(output, "profile"));
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-dev-shm-usage");
const timer = setTimeout(() => { console.error("Report output test timed out"); app.exit(1); }, 60000);
const source = '<!doctype html><html lang="en"><head><meta charset="utf-8"><style>body{margin:0;padding:0;font:20px/1.5 sans-serif;color:#102e50;background:#f5f9fc}h1{font-size:48px;color:#102e50}.page{background:#f5f9fc}.slide{background:#f5f9fc}figure{padding:24px;border-left:4px solid #187b9a}</style></head><body><section class="page slide"><h1>Report output review</h1><p>First page evidence.</p><figure>Two authored sections become two clean pages.</figure></section><section class="page slide"><h1>Next steps</h1><p>Second page evidence.</p></section></body></html>';
async function run() {
  await app.whenReady();
  const python = process.env.RATICODE_TEST_PYTHON || path.join(root, ".venv/bin/python");
  const env = { ...process.env, PYTHONPATH: path.join(root, "src") };
  execFileSync(python, ["-c", "import sys; from pathlib import Path; from gofer.ui.report_outputs import save_report; save_report(Path(sys.argv[1]), 'slides.html', sys.stdin.read(), 'slides')", output], { input: source, env });
  const win = new BrowserWindow({ show: true, width: 1280, height: 800, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } });
  await win.loadFile(path.join(output, "slides.html"));
  const js = script => win.webContents.executeJavaScript(script);
  for (let i = 0; i < 100 && !await js("innerWidth > 0"); i++) await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(await js("document.querySelector('[data-count]').textContent"), "1 / 2");
  const dimensions = await js("(()=>{const r=document.querySelector('.slide').getBoundingClientRect();return [r.width,r.height]})()");
  assert.ok(Math.abs(dimensions[0] / dimensions[1] - 16 / 9) < .01, JSON.stringify(dimensions));
  await js("document.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight'}))");
  assert.equal(await js("document.querySelector('[data-count]').textContent"), "2 / 2");
  assert.equal(await js("document.querySelectorAll('section.slide:not([hidden])').length"), 1);
  await js("document.querySelector('[data-prev]').click()");
  assert.equal(await js("document.querySelector('[data-count]').textContent"), "1 / 2");
  await js("document.dispatchEvent(new KeyboardEvent('keydown',{key:'End'}))");
  await js("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Home'}))");
  fs.writeFileSync(path.join(output, "slides.png"), (await win.webContents.capturePage()).toPNG());
  const slidePdf = await win.webContents.printToPDF({ preferCSSPageSize: true, printBackground: true });
  assert.equal((slidePdf.toString("latin1").match(/\/Type \/Page\b/g) || []).length, 2);

  const service = await startReportPdfService({ BrowserWindow, session });
  const refused = await fetch(service.env.RATICODE_REPORT_PDF_URL, { method: "POST", body: source });
  assert.equal(refused.status, 403);
  // Exercise the actual Python save path through the authenticated desktop renderer.
  await new Promise((resolve, reject) => {
    const child = spawn(python, ["-c", "import sys; from pathlib import Path; from gofer.ui.report_outputs import save_report; print(save_report(Path(sys.argv[1]), 'review.pdf', sys.stdin.read(), 'pdf')['path'])", output], { env: { ...env, ...service.env }, stdio: ["pipe", "inherit", "inherit"] });
    child.on("error", reject); child.on("exit", code => code === 0 ? resolve() : reject(new Error(`PDF writer exited ${code}`))); child.stdin.end(source);
  });
  const pdf = fs.readFileSync(path.join(output, "review.pdf"));
  assert.equal(pdf.subarray(0, 5).toString(), "%PDF-");
  assert.equal((pdf.toString("latin1").match(/\/Type \/Page\b/g) || []).length, 2);
  // Inspect PDF paint commands, not only the retained HTML: backgrounds and ink
  // must survive the actual Chromium print path.
  const streams = [...pdf.toString("latin1").matchAll(/stream\r?\n([\s\S]*?)\r?\nendstream/g)].flatMap(match => {
    try { return [inflateSync(Buffer.from(match[1], "latin1")).toString("latin1")]; }
    catch { return []; }
  }).join("\n");
  const colors = [...streams.matchAll(/([\d.]+) ([\d.]+) ([\d.]+) rg\b/g)].map(match => match.slice(1).map(Number));
  for (const rgb of [[245, 249, 252], [16, 46, 80], [24, 123, 154]]) {
    assert.ok(colors.some(color => color.every((value, index) => Math.abs(value - rgb[index] / 255) < .001)), `Missing PDF theme color ${rgb}`);
  }
  // A page section must paint the full A4 sheet, including the space below short content.
  // Chromium emits the background rectangle in CSS pixels at 96 dpi.
  const rectangles = [...streams.matchAll(/([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+) re\b/g)]
    .map(match => match.slice(1).map(Number));
  assert.ok(rectangles.some(([x, y, width, height]) => x === 0 && y === 0
    && Math.abs(width - 210 / 25.4 * 96) < 2 && Math.abs(height - 297 / 25.4 * 96) < 2),
  "Expected a full-sheet A4 background rectangle without paper margins");
  assert.ok(fs.readFileSync(path.join(output, "review.pdf.html"), "utf8").includes("Second page evidence"));
  service.server.close();
  win.destroy();
  console.log(`Report outputs passed: slide controls, 16:9 layout, two printed slides, authenticated PDF rendering, two PDF pages. Artifacts: ${output}`);
}
run().then(() => { clearTimeout(timer); app.exit(0); }).catch(error => { console.error(error); clearTimeout(timer); app.exit(1); });
