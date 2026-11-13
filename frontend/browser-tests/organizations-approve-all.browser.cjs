/* global __dirname, console, setTimeout, clearTimeout, process, fetch */
// Approve all against a real, isolated backend: Rem's requests wait, the user confirms
// the exact list, a request Rem makes after the list opened stays waiting, and the
// results say what applied. Nothing touches the user's data.
require("../electron/tests/background-display.cjs").runInBackgroundDisplay();
const { testViteCacheDir } = require("../electron/tests/test-vite-cache.cjs");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { app, BrowserWindow } = require("electron");
const root = path.resolve(__dirname, "../..");
app.setPath("userData", fs.mkdtempSync(path.join(os.tmpdir(), "organization-approve-all-profile-")));
app.disableHardwareAcceleration(); app.commandLine.appendSwitch("no-sandbox");
let backend, vite, win;
const timeout = setTimeout(() => { backend?.kill(); app.exit(1); }, 120000);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function main() {
  backend = spawn(process.env.GOFER_TEST_PYTHON || path.join(root, ".venv/bin/python"), [path.join(root, "tests/helpers/organization_requests_server.py")], {
    cwd: root, env: { ...process.env, PYTHONPATH: path.join(root, "src") },
  });
  backend.stderr.on("data", data => process.stderr.write(data));
  const lines = [];
  let output = "";
  backend.stdout.on("data", chunk => { output += chunk; const parts = output.split("\n"); output = parts.pop(); lines.push(...parts.filter(line => line.startsWith("{")).map(line => JSON.parse(line))); });
  backend.on("exit", code => { if (code) console.error(`Backend exited: ${code}`); });
  async function next(field) { const until = Date.now() + 20000; for (;;) { const found = lines.find(line => field in line); if (found) { lines.splice(lines.indexOf(found), 1); return found; } if (Date.now() > until) throw new Error(`Backend never sent ${field}`); await sleep(40); } }
  const ready = await next("port");
  const oid = ready.organizationId;
  const api = async (action, params = {}) => {
    const response = await fetch(`http://127.0.0.1:${ready.port}/api/organizations`, { method: "POST", headers: { Authorization: "Bearer organization-browser-test", "Content-Type": "application/json" }, body: JSON.stringify({ action, organizationId: oid, params }) });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error);
    return payload.result;
  };
  await app.whenReady();
  const { createServer } = await import("vite");
  vite = await createServer({ root: path.join(root, "frontend"), cacheDir: testViteCacheDir("organization-approve-all"), server: { host: "127.0.0.1", port: 0 }, plugins: [{
    name: "organization-approve-all-test",
    resolveId(id) { if (id === "/organization-approve-all-entry.jsx") return id; },
    load(id) {
      if (id === "/organization-approve-all-entry.jsx") return `import React from "react"; import {createRoot} from "react-dom/client"; import OrganizationHome from "/src/components/OrganizationHome.jsx"; import "/src/styles/index.css";
        createRoot(document.getElementById("root")).render(<main style={{height:"100vh",display:"flex"}}><OrganizationHome rootPath="" organizationId=${JSON.stringify(oid)} onSelect={() => {}} onClose={() => {}} /></main>);`;
    },
    configureServer(server) { server.middlewares.use(async (request, response, next) => {
      if (request.url === "/organization-approve-all-test") { response.setHeader("Content-Type", "text/html"); return response.end(await server.transformIndexHtml("/organization-approve-all-test", '<html><body><div id="root"></div><script type="module" src="/organization-approve-all-entry.jsx"></script></body></html>')); }
      if (!request.url.startsWith("/api/organizations")) return next();
      let body = ""; for await (const chunk of request) body += chunk;
      const upstream = await fetch(`http://127.0.0.1:${ready.port}${request.url}`, { method: request.method, headers: { Authorization: "Bearer organization-browser-test", "Content-Type": "application/json" }, ...(body ? { body } : {}) });
      response.statusCode = upstream.status; response.setHeader("Content-Type", "application/json"); response.end(await upstream.text());
    }); },
  }] });
  await vite.listen();
  win = new BrowserWindow({ width: 1280, height: 900, show: true, webPreferences: { nodeIntegration: false, contextIsolation: true } });
  const errors = []; win.webContents.on("console-message", details => { if (details.level === "error") errors.push(details.message); });
  await win.loadURL(`http://127.0.0.1:${vite.httpServer.address().port}/organization-approve-all-test`);
  const evaluate = code => win.webContents.executeJavaScript(code, true);
  async function wait(code, label = code) { const until = Date.now() + 10000; while (!await evaluate(`Boolean(${code})`)) { if (Date.now() > until) throw new Error(`Missing: ${label}\nUI: ${(await evaluate("document.body.innerText")).slice(0, 3000)}`); await sleep(40); } }
  const button = text => `[...document.querySelectorAll('button')].find(b => !b.disabled && b.textContent.trim() === ${JSON.stringify(text)})`;
  async function click(text) { await wait(button(text), `button ${text}`); await evaluate(`${button(text)}.click()`); }
  async function shot(name) { const image = await win.webContents.capturePage(); fs.writeFileSync(path.join(process.env.RATICODE_SCREENSHOT_DIR || os.tmpdir(), name), image.toPNG()); }
  const waiting = async () => Object.values((await api("read")).runtime.consentRequests).filter(r => r.status === "pending");

  // Four requests wait; each keeps its own buttons, and Approve all offers them together.
  await wait("document.querySelector('.oh-needs')");
  assert.equal((await waiting()).length, 4);
  await wait(button("Approve all 4"), "Approve all 4");
  assert.equal(await evaluate("[...document.querySelectorAll('.oh-need .acts button')].filter(b => b.textContent === 'Approve').length"), 4);

  // The first click only lists them, in the order they apply: the permission change and
  // the tickets first, starting the organization last.
  await click("Approve all 4");
  await wait("document.querySelector('.org-approve-all ol')", "confirmation list");
  const listed = await evaluate("[...document.querySelectorAll('.org-approve-all ol .org-approve-all-summary')].map(n => n.textContent)");
  assert.equal(listed.length, 4, listed.join(" | "));
  assert.match(listed[0], /permissions to workspace-write/);
  assert.match(listed[1], /approval step/); assert.match(listed[2], /approval step/);
  assert.match(listed[3], /^Start the organization/);
  assert.equal((await waiting()).length, 4, "nothing changed yet");

  // Rem asks for something else while the list is open; it's named, not added.
  backend.stdin.write("ask\n");
  const { asked } = await next("asked");
  await wait("document.querySelector('.org-approve-all')?.innerText.includes('1 newer request arrived')", "newer request note");
  await shot("organizations-approve-all-confirm.png");
  await click("Approve these 4");
  await wait("document.querySelector('.org-approve-all[aria-label=\"Approve all results\"]')", "results");
  const results = await evaluate("document.querySelector('.org-approve-all').innerText");
  assert.match(results, /Approved all 4 requests\./);
  await shot("organizations-approve-all-results.png");

  // Everything listed applied; the later request still waits, with its own buttons.
  const after = await api("read");
  assert.equal(after.runtime.state, "running");
  assert.equal(after.config.employees[0].permissionMode, "workspace-write");
  assert.ok(after.runtime.tasks.every(t => t.approvalRequired === false));
  assert.equal(after.config.employees[1].paused, true);
  assert.deepEqual((await waiting()).map(r => r.id), [asked]);
  await click("Done");
  await wait("!document.querySelector('.org-approve-all')", "results closed");
  await wait(`!${button("Approve all 1")}`, "no Approve all for one request");
  // The later request keeps its own Approve and Decline.
  assert.equal(await evaluate("[...document.querySelectorAll('.oh-need .acts button')].filter(b => b.textContent === 'Approve').length"), 1);
  await api("control", { state: "paused" });

  assert.deepEqual(errors.filter(message => !message.includes("Download the React DevTools")), []);
  console.log("Organizations Approve all browser: the confirmation lists exactly the waiting requests in their safe order, a request made after it opened stays waiting, and the results report each one against a real backend.");
}

main().then(() => { clearTimeout(timeout); win?.destroy(); vite?.close(); backend?.stdin.end(); app.exit(0); }, error => { console.error(error); clearTimeout(timeout); backend?.kill(); app.exit(1); });
