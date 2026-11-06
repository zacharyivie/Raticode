/* global __dirname, console, process, setTimeout, clearTimeout, fetch */
// Projects, report history, connections and the topic map against the real library service.
require("../electron/tests/background-display.cjs").runInBackgroundDisplay();
const { testViteCacheDir } = require("../electron/tests/test-vite-cache.cjs");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "../..");
app.setPath("userData", fs.mkdtempSync(path.join(os.tmpdir(), "brain-graph-profile-")));
app.disableHardwareAcceleration(); app.commandLine.appendSwitch("no-sandbox");
let backend, vite, win;
const timeout = setTimeout(() => { backend?.kill(); app.exit(1); }, 150000);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const screenshots = process.env.RATICODE_SCREENSHOT_DIR;

async function main() {
  backend = spawn(process.env.GOFER_TEST_PYTHON || path.join(root, ".venv/bin/python"), [path.join(root, "tests/helpers/second_brain_graph_server.py")], { cwd: root, env: { ...process.env, PYTHONPATH: path.join(root, "src") } });
  backend.stderr.on("data", data => process.stderr.write(data));
  const ready = await new Promise((resolve, reject) => {
    let output = "";
    backend.stdout.on("data", chunk => { output += chunk; if (output.includes("\n")) { try { resolve(JSON.parse(output.split("\n")[0])); } catch (error) { reject(error); } } });
    backend.on("exit", code => reject(new Error(`Backend exited: ${code}`)));
  });
  await app.whenReady();
  const { createServer } = await import("vite");
  vite = await createServer({ cacheDir: testViteCacheDir(), root: path.join(root, "frontend"), logLevel: "error", server: { host: "127.0.0.1", port: 0 }, plugins: [{
    name: "second-brain-graph-test",
    resolveId(id) { if (id === "/brain-graph-entry.jsx") return id; },
    load(id) { if (id === "/brain-graph-entry.jsx") return `import React from "react"; import {createRoot} from "react-dom/client"; import SecondBrainWorkspace from "/src/components/SecondBrainWorkspace.jsx"; import SecondBrainSidebar from "/src/components/SecondBrainSidebar.jsx"; import * as store from "/src/lib/secondBrainStore.js"; import "/src/styles/index.css"; window.__sb=store; createRoot(document.getElementById("root")).render(<main style={{height:"100vh",display:"flex"}}><aside style={{width:260,display:"flex"}}><SecondBrainSidebar active open onOpen={()=>{}}/></aside><section style={{flex:1,minWidth:0,display:"flex"}}><SecondBrainWorkspace assistant={{provider:"codex",model:"test-model"}} onClose={()=>{}}/></section></main>);`; },
    configureServer(server) { server.middlewares.use(async (request, response, next) => {
      if (request.url === "/brain-graph-test") { response.setHeader("Content-Type", "text/html"); return response.end(await server.transformIndexHtml("/brain-graph-test", '<html><body><div id="root"></div><script type="module" src="/brain-graph-entry.jsx"></script></body></html>')); }
      if (request.url.startsWith("/api/provider/capabilities")) { response.setHeader("Content-Type", "application/json"); return response.end(JSON.stringify({ providers: [] })); }
      if (!request.url.startsWith("/api/second-brains")) return next();
      let body = ""; for await (const chunk of request) body += chunk;
      const upstream = await fetch(`http://127.0.0.1:${ready.port}${request.url}`, { method: request.method, headers: { Authorization: "Bearer second-brain-browser-test", "Content-Type": "application/json" }, ...(body ? { body } : {}) });
      response.statusCode = upstream.status; response.setHeader("Content-Type", "application/json"); response.end(await upstream.text());
    }); },
  }] });
  await vite.listen();
  const url = `http://127.0.0.1:${vite.httpServer.address().port}/brain-graph-test`;
  win = new BrowserWindow({ width: 1360, height: 900, show: true, webPreferences: { contextIsolation: true, nodeIntegration: false } });
  const errors = []; win.webContents.on("console-message", details => { if (details.level === "error") { errors.push(details.message); console.error(details.message); } });
  const evaluate = code => win.webContents.executeJavaScript(code, true);
  async function wait(code, ms = 20000) { const until = Date.now() + ms; while (!await evaluate(`Boolean(${code})`)) { if (Date.now() > until) throw new Error(`Missing ${code}. UI: ${(await evaluate("document.body.innerText")).slice(0, 2500)}`); await sleep(60); } }
  const text = "document.querySelector('.sb-body')?.innerText||''";
  async function click(selector, label) { await wait(`[...document.querySelectorAll(${JSON.stringify(selector)})].some(el=>el.textContent.trim().startsWith(${JSON.stringify(label)}))`); await evaluate(`[...document.querySelectorAll(${JSON.stringify(selector)})].find(el=>el.textContent.trim().startsWith(${JSON.stringify(label)})).click()`); }
  async function capture(name) { if (!screenshots) return; await sleep(300); fs.mkdirSync(screenshots, { recursive: true }); fs.writeFileSync(path.join(screenshots, `${name}.png`), (await win.webContents.capturePage()).toPNG()); }

  await win.loadURL(url);
  await wait("__sb.getState().loaded");
  await evaluate(`__sb.createBrain({root:${JSON.stringify(ready.root)},metadata:"portable"})`);
  await wait("__sb.getState().documents.length===11");

  // Projects: folder groups, the Rem task that saved a report, and an archived conversation.
  await evaluate("__sb.navigate('projects',{projectId:''})");
  await wait(`${text}.includes('Raticode')&&${text}.includes('Taskurotta')&&${text}.includes('Not in a project')`);
  await capture("projects");
  await click(".sb-project-list button", "Raticode");
  await wait(`${text}.includes('Search repair')&&${text}.includes('Organizations audit')`);
  assert.ok(await evaluate(`${text}.includes('Seen by Raticode')`), "the task report shows it was observed");
  assert.ok(await evaluate(`${text}.includes('Rem task · Repair vector search')`), "the report shows the task that saved it");
  assert.ok(await evaluate(`${text}.includes('From its folder')`));
  await click(".sb-segmented button", "Conversations");
  await wait(`${text}.includes('Plan the organizations audit')`);
  await capture("project-conversations");

  // Merging keeps the other name as an alias and survives a reload.
  await evaluate("__sb.navigate('projects',{projectId:''})");
  await click(".sb-project-list button", "Taskurotta");
  await wait("document.querySelector('.sb-page-head h3')?.textContent==='Taskurotta'");
  await evaluate(`(()=>{const select=[...document.querySelectorAll('.sb-page-head select')].find(s=>s.querySelector('option')?.textContent==='Merge into…');const option=[...select.options].find(o=>o.textContent==='Raticode');select.value=option.value;select.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await wait("document.querySelector('.sb-page-head h3')?.textContent==='Raticode'");
  await evaluate("__sb.flushLibraryWrites()");
  await win.loadURL(url); await wait("window.__sb&&__sb.getState().loaded&&__sb.getState().projects.length===1");
  assert.deepEqual(await evaluate("__sb.getState().projects[0].aliases"), ["Taskurotta"]);
  await evaluate("__sb.navigate('projects',{projectId:''})");
  await wait(`${text}.includes('also Taskurotta')`);
  assert.equal(await evaluate("[...document.querySelectorAll('.sb-project-list strong')].some(el=>el.textContent==='Taskurotta')"), false);

  // Connections: the organization ticket read the note (observed), the other note links to it (written),
  // and similar notes are labelled as suggestions.
  const ranking = await evaluate("__sb.getState().documents.find(d=>d.path==='Notes/ranking.md').id");
  await evaluate(`__sb.openDocument(${JSON.stringify(ranking)},{preview:false})`);
  await click(".sb-segmented button, .sb-reader button", "Details");
  await wait("document.querySelector('.sb-connections')?.innerText.includes('LAB-3')");
  const connections = await evaluate("document.querySelector('.sb-connections').innerText");
  assert.match(connections, /Read for LAB-3 Tune ranking/);
  assert.match(connections, /Linked from Embedding notes/);
  assert.match(connections, /Similar documents/i);
  assert.match(connections, /Shared words/);
  await capture("connections");
  await click(".sb-connections button", "Show as graph");
  await wait("document.querySelectorAll('.sb-ego-node').length>=2");
  assert.ok(await evaluate("document.querySelectorAll('.sb-ego line[data-suggestion]').length>=1"), "suggested links are dashed");
  assert.ok(await evaluate("document.querySelectorAll('.sb-ego line:not([data-suggestion])').length>=1"), "evidence links are solid");
  await evaluate("[...document.querySelectorAll('.sb-ego-node[role=button]')].find(n=>n.getAttribute('aria-label').includes('Embedding notes')).dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))");
  await wait("__sb.viewFor(__sb.getState()).activeDocId===__sb.getState().documents.find(d=>d.path==='Notes/embedding-notes.md').id");

  // Map: topics as bubbles, keyboard selection, naming a topic, and the documents inside it.
  await evaluate("__sb.navigate('map',{mapTopicId:''})");
  await wait("document.querySelectorAll('.sb-map-topic').length>=2");
  await capture("map");
  await evaluate("document.querySelector('.sb-map-topic').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))");
  await wait("document.querySelector('.sb-map-panel')!==null");
  await evaluate(`(()=>{const input=document.querySelector('.sb-map-panel input[aria-label="Topic name"]');input.focus();Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'Release work');input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));})()`);
  await evaluate("__sb.flushLibraryWrites()");
  await wait("__sb.getState().topics.length===1");
  await wait("[...document.querySelectorAll('.sb-map-topic')].some(t=>t.getAttribute('aria-label').startsWith('Release work'))");
  await click(".sb-map-panel button", "Show its documents on the map");
  await wait("document.querySelectorAll('.sb-map-doc').length>=3");
  await capture("map-topic");
  await click(".sb-segmented button", "List");
  await wait("document.querySelector('.sb-map-page table')?.innerText.includes('Release work')");
  await win.loadURL(url); await wait("window.__sb&&__sb.getState().loaded&&__sb.getState().topics.some(t=>t.name==='Release work')");

  // Narrow windows keep the page inside the viewport.
  await evaluate("__sb.navigate('projects',{projectId:''})");
  win.setSize(900, 800); await sleep(250);
  assert.equal(await evaluate("document.documentElement.scrollWidth>innerWidth"), false);
  assert.deepEqual(errors, []);
  console.log("Second Brain projects, report history, connections and topic map passed against the real library service.");
}
main().then(async () => { clearTimeout(timeout); await vite?.close(); win?.destroy(); backend?.kill(); app.exit(0); }).catch(async error => { console.error(error); clearTimeout(timeout); await vite?.close(); backend?.kill(); app.exit(1); });
