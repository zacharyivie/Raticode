/* global __dirname, console, setTimeout, clearTimeout, process */
// Visual audit of the Second Brain sidebar and workspace with the seeded library.
// Run through run-second-brain-visual.cjs. No backend, providers or user folders are used.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "..");
const out = process.env.CAPTURE_DIR || path.join(os.tmpdir(), "raticode-second-brain-visual");
fs.mkdirSync(out, { recursive: true });
app.setPath("userData", fs.mkdtempSync(path.join(os.tmpdir(), "second-brain-capture-")));
app.disableHardwareAcceleration(); app.commandLine.appendSwitch("no-sandbox");
let vite, win;
const timeout = setTimeout(() => app.exit(1), 300000);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const entry = `import React from "react"; import { createRoot } from "react-dom/client";
import SecondBrainSidebar from "/src/components/SecondBrainSidebar.jsx";
import SecondBrainWorkspace from "/src/components/SecondBrainWorkspace.jsx";
import * as store from "/src/lib/secondBrainStore.js";
import "/src/styles/index.css";
await store.loadSampleLibrary();
window.__sb = store;
// These two screens explicitly use visual fixtures. Production requests use the library API.
const nativeFetch = window.fetch.bind(window);
window.fetch = async (input, options = {}) => {
  if (String(input).includes('/api/provider/capabilities')) return new Response(JSON.stringify({ providers: [{ id: 'claude_code', displayName: 'Claude Code', available: true, enabled: true, defaultModel: 'claude-opus-5-5', models: [{ id: 'claude-opus-5-5' }] }] }), { headers: { 'Content-Type': 'application/json' } });
  if (String(input).endsWith('/api/second-brains') && options.body) {
    const { action, params } = JSON.parse(options.body);
    if (action === 'original') {
      const doc = store.getState().documents.find(item => item.id === params.id);
      return new Response(JSON.stringify({ result: { mime: 'text/plain', content: doc.blocks.map(block => block.text).join('\\n\\n') } }), { headers: { 'Content-Type': 'application/json' } });
    }
    if (action === 'import_preview') return new Response(JSON.stringify({ result: { name: 'Visual fixture', format: 'Raticode brain package v1', size: 1024, counts: [['documents', 12], ['citations', 3]], unsupported: [], duplicates: 2, conflicts: [], losses: [] } }), { headers: { 'Content-Type': 'application/json' } });
  }
  return nativeFetch(input, options);
};
function App() {
  return <main style={{ height: "100vh", display: "flex" }}>
    <aside className="studio-sidebar bg-white" style={{ width: 272, flexShrink: 0, display: "flex", borderRight: "1px solid rgb(var(--color-line))" }}>
      <div style={{ width: 40, borderRight: "1px solid rgb(var(--color-line))" }} />
      <SecondBrainSidebar active open onOpen={() => {}} />
    </aside>
    <section style={{ flex: 1, minWidth: 0, display: "flex" }}><SecondBrainWorkspace onClose={() => {}} assistant={{ provider: "claude_code", model: "claude-opus-5-5" }} /></section>
  </main>;
}
createRoot(document.getElementById("root")).render(<App />);`;

async function main() {
  await app.whenReady();
  const { createServer } = await import("vite");
  vite = await createServer({ root, logLevel: "error", server: { host: "127.0.0.1", port: 0 }, plugins: [{
    name: "second-brain-capture",
    resolveId(id) { if (id === "/second-brain-entry.jsx") return id; },
    load(id) { if (id === "/second-brain-entry.jsx") return entry; },
    configureServer(server) { server.middlewares.use(async (request, response, next) => {
      if (!request.url.startsWith("/second-brain-test")) return next();
      response.setHeader("Content-Type", "text/html");
      response.end(await server.transformIndexHtml("/second-brain-test", '<html><body><div id="root"></div><script type="module" src="/second-brain-entry.jsx"></script></body></html>'));
    }); },
  }] });
  await vite.listen();
  const base = `http://127.0.0.1:${vite.httpServer.address().port}/second-brain-test`;
  win = new BrowserWindow({ width: 1440, height: 900, show: true, webPreferences: { nodeIntegration: false, contextIsolation: true } });
  const errors = [];
  win.webContents.on("console-message", details => { if (details.level === "error") { errors.push(details.message); console.error(`[page] ${details.message}`); } });
  const evaluate = code => win.webContents.executeJavaScript(code, true);
  async function clickText(text, selector = "button") {
    const ok = await evaluate(`(() => { const el = [...document.querySelectorAll(${JSON.stringify(selector)})].find(b => b.textContent.trim() === ${JSON.stringify(text)} || b.getAttribute('aria-label') === ${JSON.stringify(text)}); if (!el) return false; el.click(); return true; })()`);
    assert.ok(ok, `Missing control: ${text}`); await sleep(300);
  }
  async function shot(name) {
    await sleep(350);
    const overflow = await evaluate("document.documentElement.scrollWidth > innerWidth || [...document.querySelectorAll('.sb-workspace *, .sb-sidebar *')].filter(e => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX === 'visible' && e.getBoundingClientRect().right > innerWidth + 1).map(e => e.className).slice(0, 3)");
    assert.ok(overflow === false || (Array.isArray(overflow) && overflow.length === 0), `Overflow in ${name}: ${JSON.stringify(overflow)}`);
    fs.writeFileSync(path.join(out, `${name}.png`), (await win.webContents.capturePage()).toPNG());
  }
  const scenes = (process.env.CAPTURE_SCENES || "search,reader,filters,details,original,collection,compare,report,builder,import,export,health,inbox,jobs,settings,switcher,empty,toast").split(",");
  for (const theme of (process.env.CAPTURE_THEMES || "light,dark").split(",")) {
    for (const width of (process.env.CAPTURE_WIDTHS || "1440,980").split(",").map(Number)) {
      for (const scene of scenes) {
        win.setSize(width, 900);
        await win.loadURL(`${base}?scene=${scene}`); await sleep(900);
        await evaluate(`document.documentElement.classList.toggle('dark', ${theme === "dark"}); window.__sb.ensureLibrary()`); await sleep(300);
        const tag = `${theme}-${width}-${scene}`;
        if (scene === "search") { await evaluate(`__sb.updateSearch({ text: '"event journal" OR snapshot', filters: { ...__sb.getState().views['brain-research']?.search?.filters, tags: ['architecture'] } })`); await shot(tag); }
        if (scene === "reader") { await evaluate("__sb.updateSearch({ text: 'citation' }); __sb.openDocument('doc-citation-ux', { blockId: 'cux-2', preview: false }); __sb.openDocument('doc-w3c')"); await shot(tag); }
        if (scene === "filters") { await evaluate("__sb.updateSearch({ text: 'search published:2009..2026 colour:red' })"); await clickText("More filters filter"); await shot(tag); }
        if (scene === "details") { await evaluate("__sb.openDocument('doc-index-review', { preview: false })"); await clickText("Details"); await shot(tag); }
        if (scene === "original") { await evaluate("__sb.openDocument('doc-second-brain-report', { preview: false })"); await clickText("Original"); await shot(tag); }
        if (scene === "collection") { await evaluate("__sb.navigate('collections', { collectionId: 'col-launch', collectionTab: 'evidence' })"); await shot(tag); }
        if (scene === "compare") { await evaluate("__sb.navigate('collections', { collectionId: 'col-launch', collectionTab: 'compare' })"); await shot(tag); }
        if (scene === "report") { await evaluate("__sb.navigate('reports', { reportId: 'rep-citations' })"); await shot(tag); await clickText("Claims"); await shot(`${tag}-claims`); await clickText("Provenance"); await shot(`${tag}-provenance`); }
        if (scene === "builder") {
          // Unbroken titles (such as content hashes) must wrap inside the source list instead of covering the options column.
          await evaluate("__sb.updateDocument('doc-w3c', { title: 'eb651dccc2f1215d702b49975d4a94cee6dff25069a65de3c2a08f4288410de1' }); __sb.openDialog('report-builder', { collectionId: 'col-launch' })");
          const overlap = await evaluate("(() => { const picker = document.querySelector('.sb-source-picker').getBoundingClientRect(); const side = document.querySelector('.sb-builder-side').getBoundingClientRect(); return picker.right > side.left + 1 && side.top < picker.bottom ? [picker.right, side.left] : null; })()");
          assert.equal(overlap, null, `Source list overlaps report options in ${tag}`);
          // Focused text fields show one soft ring, not the global outline stacked on top of it.
          assert.equal(await evaluate("document.activeElement.tagName + ' ' + getComputedStyle(document.activeElement).outlineStyle"), "TEXTAREA none", `Doubled focus ring in ${tag}`);
          await shot(tag);
        }
        if (scene === "import") { await evaluate("__sb.openDialog('import')"); await sleep(200); await evaluate("(() => { const i = document.querySelector('.sb-modal input'); i.focus(); })()"); await win.webContents.insertText("/tmp/field-research.zip"); await evaluate("document.querySelector('.sb-modal input').blur()"); await clickText("Preview import"); await shot(tag); }
        if (scene === "export") { await evaluate("__sb.openDialog('export')"); await sleep(200); assert.equal(await evaluate("document.activeElement.classList.contains('sb-modal') && getComputedStyle(document.activeElement).outlineStyle"), "none", `Focus ring around the whole dialog in ${tag}`); await evaluate("[...document.querySelectorAll('.sb-modal input[type=checkbox]')][0].click()"); await shot(tag); }
        if (scene === "health") { await evaluate("__sb.navigate('health')"); await shot(tag); }
        if (scene === "inbox") { await evaluate("__sb.navigate('inbox')"); await shot(tag); }
        if (scene === "jobs") { await evaluate("__sb.navigate('jobs')"); await clickText("New job"); await shot(tag); }
        if (scene === "settings") { await evaluate("__sb.navigate('settings')"); await shot(tag); }
        if (scene === "switcher") { await evaluate("document.querySelector('.sb-switcher-trigger').click()"); await shot(tag); }
        if (scene === "toast") { await evaluate("__sb.navigate('reports', { reportId: 'rep-citations' }); __sb.notify('Added passage to “Second Brain launch evidence”.')"); await shot(tag); }
        if (scene === "empty") { await evaluate("__sb.updateSearch({ text: 'nonexistentword', filters: { ...__sb.getState().views['brain-research']?.search?.filters, types: ['pdf'] } })"); await shot(tag); await evaluate("__sb.setActiveBrain('brain-thesis')"); await shot(`${tag}-unavailable`); }
      }
    }
  }
  assert.deepEqual(errors, []);
  console.log(`Second Brain visual checks passed. Screenshots: ${out}`);
}
main().then(async () => { clearTimeout(timeout); await vite?.close(); win?.destroy(); app.exit(0); }).catch(async error => { console.error(error); clearTimeout(timeout); await vite?.close(); app.exit(1); });
